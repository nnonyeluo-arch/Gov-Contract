"""
Travis County Procurement Scraper
Travis County migrated from SAP Ariba to BidNet Direct.
Portal: https://www.bidnetdirect.com/texas/traviscounty
BidNet Direct may have a public API; we also attempt HTML scraping as fallback.
"""

import os
import time
import re
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PORTAL_URL = "https://www.bidnetdirect.com/texas/traviscounty"
BIDNET_API_ENDPOINTS = [
    "https://www.bidnetdirect.com/api/bids/traviscounty",
    "https://www.bidnetdirect.com/api/v1/bids?agency=traviscounty&status=open",
    "https://www.bidnetdirect.com/api/solicitations?state=TX&agency=travis-county",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Referer": PORTAL_URL,
}


def fetch_bidnet_api() -> list[dict]:
    for endpoint in BIDNET_API_ENDPOINTS:
        try:
            resp = httpx.get(endpoint, headers=HEADERS, timeout=20, follow_redirects=True)
            if resp.status_code in (400, 401, 403, 404, 405):
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data.get("bids") or data.get("solicitations") or data.get("data") or data.get("results") or []
                    if items:
                        print(f"[travis_county] BidNet API returned {len(items)} items from {endpoint}")
                        return items
                except Exception:
                    pass
        except Exception as e:
            print(f"[travis_county] BidNet API {endpoint} error: {e}")
    return []


def fetch_html_scrape() -> list[dict]:
    try:
        resp = httpx.get(PORTAL_URL, headers={**HEADERS, "Accept": "text/html"}, timeout=25, follow_redirects=True)
        if resp.status_code == 200:
            return _parse_html(resp.text)
        print(f"[travis_county] HTML scrape status {resp.status_code}")
    except Exception as e:
        print(f"[travis_county] HTML scrape error: {e}")
    return []


def _parse_html(html: str) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        items = []
        for row in soup.select("table tr, .bid-row, .solicitation-row, li.bid"):
            link = row.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            href = link["href"]
            if not href.startswith("http"):
                href = f"https://www.bidnetdirect.com{href}"
            all_text = row.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            bid_id_match = re.search(r"[A-Z0-9]{2,}-\d+|\d{4}-[A-Z0-9]+-\d+", all_text)
            bid_id = bid_id_match.group(0) if bid_id_match else ""
            items.append({"id": bid_id or title[:40], "title": title, "url": href, "due_date": dates[-1] if dates else None})
        return items
    except Exception as e:
        print(f"[travis_county] HTML parse error: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    bid_id = item.get("id") or item.get("bid_id") or item.get("solicitation_number") or ""
    title = (item.get("title") or item.get("name") or item.get("description") or "").strip()
    if not title:
        return None
    url = item.get("url") or item.get("link") or PORTAL_URL
    due_date = None
    for field in ["due_date", "closing_date", "close_date", "dueDate", "closingdate"]:
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
        raw_v = item.get(field)
        if raw_v:
            try:
                value = float(str(raw_v).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass
    return {
        "source": "travis_county",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(item.get("department") or "Travis County")[:300],
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
    print("[travis_county] Starting scrape...")
    items = fetch_bidnet_api()
    if not items:
        print("[travis_county] BidNet API returned nothing, trying HTML scrape...")
        items = fetch_html_scrape()
    print(f"[travis_county] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[travis_county] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "travis_county", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": None if items else "BidNet Direct returned 0 items",
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
