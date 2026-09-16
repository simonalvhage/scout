"""Vinted: scraping via vinted_scraper, en gång per unik sökning."""

import json
import logging
import time
from pathlib import Path

from scout.common import now, process_users
from scout.db import DATA_DIR, get_vinted_users

log = logging.getLogger(__name__)

PLATFORM = "Vinted"
VINTED_BASE_URL = "https://www.vinted.se"
PER_PAGE = 48
SLEEP_BETWEEN_SEARCHES = 3  # sekunder, var snäll mot Vinted/Datadome


# ---------------------------------------------------------------------------
# Vinted scraper
# ---------------------------------------------------------------------------

def search_key(params: dict) -> str:
    return json.dumps(params, sort_keys=True, ensure_ascii=False)


def format_price(amount, currency) -> str:
    if amount is None:
        return "Okänt pris"
    return f"{amount:g} {currency or ''}".strip()


def scrape_vinted(unique_searches: list[dict]) -> dict[str, list[dict]]:
    """Returnerar {search_key: [listings]}."""
    from vinted_scraper import VintedScraper

    scraper = VintedScraper(VINTED_BASE_URL)
    results = {}

    for search in unique_searches:
        key = search_key(search)
        params = {"order": "newest_first", "per_page": PER_PAGE, "page": 1, **search}
        log.info(f"Searching: {params}")
        results[key] = []

        try:
            items = scraper.search(params)
        except Exception as e:
            log.error(f"Search failed for {search}: {e}")
            time.sleep(SLEEP_BETWEEN_SEARCHES)
            continue

        for item in items:
            if item.id is None:
                continue
            listing_id = str(item.id)
            details = " · ".join(
                x for x in [
                    item.brand_title,
                    item.size_title,
                    item.status,
                    f"inkl. köparskydd {format_price(item.total_item_price, item.currency)}"
                    if item.total_item_price is not None else None,
                ] if x
            )
            results[key].append({
                "id": listing_id,
                "url": item.url or f"{VINTED_BASE_URL}/items/{listing_id}",
                "search": search.get("search_text", ""),
                "title": item.title or "?",
                "price": format_price(item.price, item.currency),
                "details": details,
                "image": item.photos[0].url if item.photos else None,
                "scraped_at": now().isoformat(),
            })

        log.info(f"{len(results[key])} listings for {search}")
        time.sleep(SLEEP_BETWEEN_SEARCHES)

    return results


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(skip_ai: bool = False, skip_email: bool = False) -> dict:
    users = get_vinted_users()
    if not users:
        log.info("No active users with Vinted searches.")
        return {}

    unique = {}
    for u in users:
        for s in u["searches"]:
            unique[search_key(s)] = s
    log.info(f"{len(unique)} unique searches for {len(users)} users")

    results = scrape_vinted(list(unique.values()))

    listings_by_user = {}
    for u in users:
        merged = {}
        for s in u["searches"]:
            for l in results.get(search_key(s), []):
                merged.setdefault(l["id"], l)
        listings_by_user[u["id"]] = [dict(l) for l in merged.values()]

    data = Path(DATA_DIR)
    return process_users(
        PLATFORM, users, listings_by_user,
        seen_file=data / "vinted_seen.json", out_dir=data / "reports",
        skip_ai=skip_ai, skip_email=skip_email,
    )
