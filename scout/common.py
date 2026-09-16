"""
Gemensam logik för Vinted och Marketplace (fleranvändare).

- Seen-listor per användare
- OpenAI-analys med användarens intressen
- HTML-rapport per användare
- Mejl via SMTP

Environment variables:
    OPENAI_API_KEY                      — OpenAI API key
    OPENAI_MODEL                        — default gpt-4o-mini
    SMTP_HOST, SMTP_PORT, SMTP_USER,
    SMTP_PASS, SMTP_FROM (valfri)       — SMTP för rapportmejl (STARTTLS)
"""

import json
import logging
import os
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

AI_BATCH_SIZE = 10
SEEN_MAX_PER_USER = 5000
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Stockholm"))


def now() -> datetime:
    return datetime.now(TZ)


# ---------------------------------------------------------------------------
# Seen listings (per user)
# ---------------------------------------------------------------------------

def load_seen(path: Path) -> dict:
    """Returnerar {user_id: [ids]}. En gammal platt lista ignoreras."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def save_seen(path: Path, seen: dict):
    path.write_text(json.dumps(seen, indent=2))


def filter_new_for_user(seen: dict, user_id, listings: list[dict]) -> tuple[list[dict], bool]:
    """Returnerar (nya annonser, first_run).

    Första körningen för en ny användare fyller bara seen-listan, så ingen
    får en flod av redan befintliga annonser.
    """
    key = str(user_id)
    first_run = key not in seen
    ids = seen.setdefault(key, [])
    known = set(ids)
    new = []
    for l in listings:
        if l["id"] not in known:
            new.append(l)
            ids.append(l["id"])
            known.add(l["id"])
    seen[key] = ids[-SEEN_MAX_PER_USER:]
    return new, first_run


# ---------------------------------------------------------------------------
# OpenAI analysis
# ---------------------------------------------------------------------------

def analyze_with_openai(listings: list[dict], interests: str, platform: str) -> list[dict]:
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.error("OPENAI_API_KEY not set — skipping AI analysis")
        return listings

    client = OpenAI(api_key=api_key)

    for batch_start in range(0, len(listings), AI_BATCH_SIZE):
        batch = listings[batch_start : batch_start + AI_BATCH_SIZE]

        listings_text = ""
        for i, l in enumerate(batch):
            listings_text += (
                f"\n--- Listing {i+1} ---\n"
                f"Titel: {l.get('title', '?')}\n"
                f"Pris: {l.get('price', '?')}\n"
                f"Detaljer: {l.get('details', '')}\n"
                f"URL: {l['url']}\n"
            )

        prompt = f"""Analysera dessa {platform}-annonser.

FÖR VARJE annons, bedöm:
1. Prisvärdhet (1-10): Är priset bra? 10 = fantastiskt fynd.
2. Relevans (1-10): Hur relevant är detta för köparen? 10 = perfekt match.
3. Rekommendation: "KÖP" / "KANSKE" / "SKIPPA"
4. Kommentar: Kort motivering (max 2 meningar).

Köparens intressen:
{interests}

Annonser:
{listings_text}

Svara ENBART med JSON (ingen markdown):
[
  {{
    "listing_index": 1,
    "price_score": 7,
    "relevance_score": 8,
    "recommendation": "KÖP",
    "comment": "Bra pris för en NAS-disk."
  }}
]"""

        try:
            response = client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": "Du är en expert på second-hand-priser i Sverige. Svara med ren JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )

            raw = response.choices[0].message.content.strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            analyses = json.loads(raw)

            for analysis in analyses:
                idx = analysis["listing_index"] - 1
                if 0 <= idx < len(batch):
                    batch[idx]["ai_analysis"] = {
                        "price_score": analysis.get("price_score", 0),
                        "relevance_score": analysis.get("relevance_score", 0),
                        "recommendation": analysis.get("recommendation", "?"),
                        "comment": analysis.get("comment", ""),
                    }

            log.info(f"AI analyzed batch {batch_start // AI_BATCH_SIZE + 1}")

        except Exception as e:
            log.error(f"OpenAI analysis failed: {e}")
            for l in batch:
                l["ai_analysis"] = {"error": str(e)}

        time.sleep(1)

    return listings


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def generate_report(listings: list[dict], platform: str) -> tuple[int, str]:
    """Returns (interesting_count, html)."""
    order = {"KÖP": 0, "KANSKE": 1, "SKIPPA": 2}
    filtered = [l for l in listings if l.get("ai_analysis", {}).get("price_score", 0) >= 9]
    sorted_listings = sorted(
        filtered,
        key=lambda l: order.get(l.get("ai_analysis", {}).get("recommendation", ""), 3),
    )
    interesting = [
        l for l in sorted_listings
        if l.get("ai_analysis", {}).get("recommendation") in ("KÖP", "KANSKE")
    ]

    now = now().strftime("%Y-%m-%d %H:%M")

    html_rows = ""
    for l in sorted_listings:
        ai = l.get("ai_analysis", {})
        rec = ai.get("recommendation", "?")
        color = {"KÖP": "#22c55e", "KANSKE": "#f59e0b", "SKIPPA": "#94a3b8"}.get(rec, "#ccc")
        img = (
            f'<img src="{escape(l["image"])}" width="60" style="border-radius: 4px;">'
            if l.get("image") else ""
        )
        html_rows += f"""
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px;">
                <span style="background: {color}; color: white; padding: 2px 8px;
                             border-radius: 4px; font-weight: bold; font-size: 13px;">{escape(rec)}</span>
            </td>
            <td style="padding: 10px;">{img}</td>
            <td style="padding: 10px;">
                <a href="{escape(l['url'])}" style="color: #2563eb; text-decoration: none; font-weight: 500;">
                    {escape(l.get('title', '?'))}
                </a>
                <div style="color: #666; font-size: 12px;">{escape(l.get('details', ''))}</div>
            </td>
            <td style="padding: 10px; font-weight: bold; white-space: nowrap;">{escape(l.get('price', '?'))}</td>
            <td style="padding: 10px; font-size: 13px;">
                P: {ai.get('price_score', '-')}/10 &nbsp; R: {ai.get('relevance_score', '-')}/10
            </td>
            <td style="padding: 10px; font-size: 13px; color: #555;">{escape(str(ai.get('comment', '')))}</td>
        </tr>"""

    html = f"""<html><body style="font-family: -apple-system, sans-serif; max-width: 1000px; margin: 0 auto;">
    <h2 style="color: #1e293b;">{escape(platform)} Scout — {now}</h2>
    <p style="color: #64748b;">{len(interesting)} intressanta av {len(listings)} nya annonser</p>
    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <tr style="background: #f8fafc; text-align: left;">
            <th style="padding: 10px;">Rec</th>
            <th style="padding: 10px;"></th>
            <th style="padding: 10px;">Titel</th>
            <th style="padding: 10px;">Pris</th>
            <th style="padding: 10px;">Score</th>
            <th style="padding: 10px;">Kommentar</th>
        </tr>
        {html_rows}
    </table>
    <p style="color: #94a3b8; font-size: 12px; margin-top: 20px;">
        Genererat av Scout. Ändra dina sökord och intressen i Scout-GUI:t.
    </p>
    </body></html>"""

    return len(interesting), html


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, html: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    if not host:
        log.warning("SMTP_HOST not set — skipping email")
        return False

    user = os.environ.get("SMTP_USER", "")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM") or user
    msg["To"] = to
    msg.set_content("Din e-postklient visar inte HTML. Öppna mejlet i en annan klient.")
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, os.environ.get("SMTP_PASS", ""))
            smtp.send_message(msg)
        log.info(f"Email sent to {to}")
        return True
    except Exception as e:
        log.error(f"Email to {to} failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Per-user pipeline
# ---------------------------------------------------------------------------

def process_users(
    platform: str,
    users: list[dict],
    listings_by_user: dict,
    seen_file: Path,
    out_dir: Path,
    skip_ai: bool = False,
    skip_email: bool = False,
) -> dict:
    """Seen-filter → AI → rapport → mejl, en gång per användare.

    Returnerar en summering {user_id: {"new": n, "interesting": n, "emailed": bool}}.
    """
    seen = load_seen(seen_file)
    summary = {}

    for user in users:
        uid = user["id"]
        listings = listings_by_user.get(uid, [])
        new, first_run = filter_new_for_user(seen, uid, listings)
        save_seen(seen_file, seen)  # spara direkt, så en krasch inte ger dubbletter
        log.info(f"[user {uid}] Total: {len(listings)} | New: {len(new)} | First run: {first_run}")

        result = {"new": len(new), "interesting": 0, "emailed": False, "first_run": first_run}
        summary[uid] = result
        if first_run or not new:
            continue

        if not skip_ai:
            new = analyze_with_openai(new, user["interests"], platform)

        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{platform.lower()}_user{uid}"
        (out_dir / f"{prefix}_listings.json").write_text(json.dumps(new, indent=2, ensure_ascii=False))

        count, html = generate_report(new, platform)
        (out_dir / f"{prefix}_report.html").write_text(html)
        result["interesting"] = count

        if count > 0 and not skip_email and user.get("email"):
            subject = f"{platform} Scout — {count} nya fynd {now().strftime('%Y-%m-%d %H:%M')}"
            result["emailed"] = send_email(user["email"], subject, html)

    log.info(f"{platform} summary: {json.dumps(summary)}")
    return summary
