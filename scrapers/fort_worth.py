"""
City of Fort Worth Procurement Scraper
Fort Worth uses Bonfire/Euna Procurement at fortworthtexas.bonfirehub.com.
The portal is a JS-rendered React SPA — no public unauthenticated API exists.
Strategy:
  1. Playwright network intercept — capture the XHR call Bonfire makes internally
  2. Playwright HTML scrape — render the page, parse opportunity cards
  3. Fort Worth Open Data fallback (if available)
"""

import os
import re
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BONFIRE_URL = "https://fortworthtexas.bonfirehub.com/portal/?tab=openOpportunities"
PORTAL_URL = BONFIRE_URL

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
}


def fetch_bonfire_playwright() -> list[dict]:
    """Use Playwright to intercept Bonfire's internal API calls."""
    try:
        from playwright_helper import get_network_json
        print("[fort_worth] Trying Playwright network intercept on Bonfire...")
        for pattern in ["opportunit", "solicitation", "bonfire", "api"]:
            items = get_network_json(BONFIRE_URL, api_pattern=pattern, wait_seconds=8)
            if items:
                print(f"[fort_worth] Playwright intercepted {len(items)} items (pattern: {pattern})")
                return items
    except ImportError:
        print("[fort_worth] Playwright not available")
    except Exception as e:
        print(f"[fort_worth] Playwright intercept error: {e}")
    return []


def fetch_bonfire_html_playwright() -> list[dict]:
    """Playwright HTML scrape — render the Bonfire page and parse opportunity cards."""
    try:
        from playwright_helper import get_page_html
        print("[fort_worth] Trying Playwright HTML scrape of Bonfire...")
        html = get_page_html(
            BONFIRE_URL,
            wait_selector=".opportunity-card, [class*='opportunity'], [class*='Opportunity'], table tr, .MuiCard-root",
            wait_seconds=6,
        )
        if html:
            return _parse_bonfire_html(html)
    except ImportError:
        print("[fort_worth] Playwright not available for HTML scrape")
    except Exception as e:
        print(f"[fort_worth] Playwright HTML scrape error: {e}")
    return []


def _parse_bonfire_html(html: str) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        items = []
        cards = (
            soup.select("[class*='opportunity']")
            or soup.select("[class*='Opportunity']")
            or soup.select(".MuiCard-root")
            or soup.select("table tr")
            or soup.select("li")
        )
        for card in cards:
            link = card.find("a", href=True)
            title_el = card.find(["h2", "h3", "h4", "strong"])
            title_src = link or title_el or card
            title = title_src.get_text(strip=True)[:300] if title_src else ""
            if not title or len(title) < 5:
                continue
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://fortworthtexas.bonfirehub.com{href}"
            all_text = card.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            bid_id_match = re.search(r"[A-Z0-9]{2,}-\d+", all_text)
            items.append({
                "id": bid_id_match.group(0) if bid_id_match else title[:40],
                "title": title,
                "url": href or PORTAL_URL,
                "due_date": dates[-1] if dates else None,
            })
        return items
    except Exception as e:
        print(f"[fort_worth] Bonfire HTML parse error: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    bid_id = item.get("id") or item.get("bid_id") or item.get("solicitation_number") or item.get("number") or ""
    title = (item.get("title") or item.get("name") or item.get("description") or "").strip()
    if not title:
        return None

    url = item.get("url") or item.get("link") or PORTAL_URL

    due_date = None
    for field in ["due_date", "closing_date", "close_date", "dueDate", "closeDate"]:
        raw = item.get(field)
        if raw:
            try:
                raw_str = str(raw).strip()
                if "T" in raw_str:
                    due_date = raw_str[:10]
                elif "/" in raw_str:
                    parts = raw_str.split("/")
                    if len(parts) == 3:
                        m, d, y = parts
                        due_date = f"{y.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
                else:
                    due_date = raw_str[:10]
                break
            except Exception:
                pass

    value = None
    for field in ["estimated_value", "value", "amount", "budget"]:
        raw = item.get(field)
        if raw:
            try:
                value = float(str(raw).replace(",", "").replace("$", "").strip())
                break
            except (ValueError, TypeError):
                pass

    return {
        "source": "fort_worth",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(item.get("department") or "City of Fort Worth")[:300],
        "naics": str(item.get("naics") or ""),
        "value": value,
        "due_date": due_date,
        "set_aside": str(item.get("set_aside") or ""),
        "url": url,
        "raw_html": str(item.get("description") or "")[:5000],
    }


def upsert_contracts(contracts):
    if not contracts:
        return 0
    result = supabase.table("contracts").upsert(contracts, on_conflict="source,source_id", ignore_duplicates=True).execute()
    return len(result.data) if result.data else 0


def run():
    start = time.time()
    print("[fort_worth] Starting scrape...")
    items = fetch_bonfire_playwright()
    if not items:
        items = fetch_bonfire_html_playwright()
    print(f"[fort_worth] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[fort_worth] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "fort_worth", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": None if items else "Bonfire returned 0 — Playwright may not be installed",
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
