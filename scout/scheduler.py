"""Enkel schemaläggare i en bakgrundstråd (ersätter Jenkins cron).

Environment variables:
    TZ                  — tidszon (default Europe/Stockholm)
    RUN_HOURS           — timmar att köra, kommaseparerat (default 8,10,12,14,16,18,20,22)
    RUN_MINUTE          — minut efter hel timme (default 5)
    RUN_ON_START        — "true" = kör direkt när containern startar (default false)
    ENABLE_VINTED       — default true
    ENABLE_MARKETPLACE  — default true
    ADMIN_EMAIL         — får mejl om ett jobb kraschar (valfri)
"""

import logging
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scout.common import send_email

log = logging.getLogger(__name__)

TZ = ZoneInfo(os.environ.get("TZ", "Europe/Stockholm"))
RUN_HOURS = sorted({int(h) for h in os.environ.get("RUN_HOURS", "8,10,12,14,16,18,20,22").split(",") if h.strip()})
RUN_MINUTE = int(os.environ.get("RUN_MINUTE", "5"))
RUN_ON_START = os.environ.get("RUN_ON_START", "false").lower() == "true"
ENABLE_VINTED = os.environ.get("ENABLE_VINTED", "true").lower() == "true"
ENABLE_MARKETPLACE = os.environ.get("ENABLE_MARKETPLACE", "true").lower() == "true"
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

_lock = threading.Lock()
_started = False
_status = {"next_run": None, "last_run": None, "last_result": "Ingen körning ännu", "running": False}


def next_run_time(now: datetime) -> datetime:
    for day in range(2):
        base = (now + timedelta(days=day)).replace(minute=RUN_MINUTE, second=0, microsecond=0)
        for hour in RUN_HOURS:
            candidate = base.replace(hour=hour)
            if candidate > now:
                return candidate
    raise RuntimeError("RUN_HOURS is empty")


def _jobs():
    jobs = []
    if ENABLE_VINTED:
        from scout import vinted
        jobs.append(("Vinted", vinted.run))
    if ENABLE_MARKETPLACE:
        from scout import marketplace
        jobs.append(("Marketplace", marketplace.run))
    return jobs


def run_all():
    if not _lock.acquire(blocking=False):
        log.warning("Previous run still in progress — skipping")
        return
    _status["running"] = True
    results = []
    try:
        for name, fn in _jobs():
            started = time.monotonic()
            try:
                log.info(f"=== {name} start ===")
                summary = fn()
                mailed = sum(1 for r in summary.values() if r.get("emailed"))
                results.append(f"{name}: OK, {mailed} mejl")
                log.info(f"=== {name} done in {time.monotonic() - started:.0f}s ===")
            except Exception:
                tb = traceback.format_exc()
                log.error(f"{name} failed:\n{tb}")
                results.append(f"{name}: FEL")
                if ADMIN_EMAIL:
                    send_email(ADMIN_EMAIL, f"Scout: {name} misslyckades", f"<pre>{tb}</pre>")
    finally:
        _status["last_run"] = datetime.now(TZ)
        _status["last_result"] = ", ".join(results) or "Inga jobb aktiverade"
        _status["running"] = False
        _lock.release()


def _loop():
    if RUN_ON_START:
        run_all()
    while True:
        target = next_run_time(datetime.now(TZ))
        _status["next_run"] = target
        log.info(f"Next run: {target:%Y-%m-%d %H:%M %Z}")
        while (remaining := (target - datetime.now(TZ)).total_seconds()) > 0:
            time.sleep(min(remaining, 60))
        run_all()


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="scout-scheduler", daemon=True).start()


def status_text() -> str:
    if _status["running"]:
        return "Sökning pågår just nu."
    parts = []
    if _status["last_run"]:
        parts.append(f"Senaste körning {_status['last_run']:%H:%M} ({_status['last_result']})")
    if _status["next_run"]:
        parts.append(f"nästa {_status['next_run']:%H:%M}")
    return ", ".join(parts) or "Schemaläggaren startar…"
