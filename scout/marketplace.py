"""Facebook Marketplace: scraping via Playwright (ingen login), en gång per unik URL."""

import logging
import time
from pathlib import Path

from scout.common import now, process_users
from scout.db import DATA_DIR, get_fb_users

log = logging.getLogger(__name__)

PLATFORM = "Marketplace"


# ---------------------------------------------------------------------------
# Playwright scraper
# ---------------------------------------------------------------------------

def scrape_marketplace(urls: list[str]) -> dict[str, list[dict]]:
    """Returnerar {url: [listings]}."""
    from playwright.sync_api import sync_playwright
    from bs4 import BeautifulSoup

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = browser.new_context(
            locale="sv-SE",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        for url in urls:
            log.info(f"Scraping: {url}")
            results[url] = []
            page = context.new_page()

            try:
                page.goto(url, timeout=30_000)
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
                time.sleep(2)
            except Exception as e:
                log.error(f"Failed to load {url}: {e}")
                page.close()
                continue

            # Dismiss cookie banner
            for selector in [
                '[data-cookiebanner="accept_button"]',
                'button[data-testid="cookie-policy-manage-dialog-accept-button"]',
                '[aria-label="Allow all cookies"]',
                '[aria-label="Tillåt alla cookies"]',
            ]:
                try:
                    btn = page.locator(selector)
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        time.sleep(1)
                        break
                except Exception:
                    continue

            # Dismiss login modal
            for strategy in [
                lambda: page.locator('[aria-label="Close"]').click(),
                lambda: page.locator('[aria-label="Stäng"]').click(),
                lambda: page.keyboard.press("Escape"),
                lambda: page.mouse.click(10, 10),
            ]:
                try:
                    strategy()
                    time.sleep(0.5)
                    break
                except Exception:
                    continue

            for _ in range(3):
                page.mouse.wheel(0, 1500)
                time.sleep(1)

            html = page.content()
            page.close()

            seen_ids = set()
            soup = BeautifulSoup(html, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                if "/marketplace/item/" not in href:
                    continue

                parts = href.split("/marketplace/item/")
                if len(parts) < 2:
                    continue

                listing_id = parts[1].strip("/").split("/")[0].split("?")[0]
                if not listing_id.isdigit() or listing_id in seen_ids:
                    continue
                seen_ids.add(listing_id)

                texts = [t.strip() for t in link.stripped_strings if t.strip()]
                if len(texts) < 2:
                    continue

                listing = {
                    "id": listing_id,
                    "url": f"https://www.facebook.com/marketplace/item/{listing_id}/",
                    "source_url": url,
                    "scraped_at": now().isoformat(),
                }

                price_idx = None
                for i, text in enumerate(texts):
                    if "kr" in text.lower() or "gratis" in text.lower() or text[0].isdigit():
                        price_idx = i
                        break

                location = ""
                if price_idx is not None:
                    listing["price"] = texts[price_idx]
                    listing["title"] = texts[price_idx + 1] if price_idx + 1 < len(texts) else "?"
                    if len(texts) > price_idx + 2:
                        location = texts[-1]
                else:
                    listing["title"] = texts[0]
                    location = texts[1] if len(texts) > 1 else ""
                    listing["price"] = "Okänt pris"

                listing["details"] = location
                results[url].append(listing)

            log.info(f"{len(results[url])} listings from {url}")

        browser.close()

    return results


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(skip_ai: bool = False, skip_email: bool = False) -> dict:
    users = get_fb_users()
    if not users:
        log.info("No active users with Marketplace searches.")
        return {}

    unique_urls = list(dict.fromkeys(url for u in users for url in u["urls"]))
    log.info(f"{len(unique_urls)} unique URLs for {len(users)} users")

    results = scrape_marketplace(unique_urls)

    listings_by_user = {}
    for u in users:
        merged = {}
        for url in u["urls"]:
            for l in results.get(url, []):
                merged.setdefault(l["id"], l)
        listings_by_user[u["id"]] = [dict(l) for l in merged.values()]

    data = Path(DATA_DIR)
    return process_users(
        PLATFORM, users, listings_by_user,
        seen_file=data / "marketplace_seen.json", out_dir=data / "reports",
        skip_ai=skip_ai, skip_email=skip_email,
    )
