"""
TxSmartBuy / Texas ESBD Scraper
The ESBD at txsmartbuy.gov/esbd is a search form with div-based results (no table).
Each bid has: title link, Solicitation ID, Due Date, Agency/Member Number, Status.
We filter for Status=Posted (active bids) and scrape pages 1-5 (~100 bids).
Also tries CSV export endpoint for cleaner bulk data.
"""

import os
import re
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_URL = "https://www.txsmartbuy.gov/esbd"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": BASE_URL,
}


def fetch_csv() -> list[dict]:
    """Try the CSV export endpoint for bulk clean data."""
    csv_urls = [
        "https://www.txsmartbuy.gov/esbd/export?status=Posted&format=csv",
        "https://www.txsmartbuy.gov/esbd?status=Posted&export=csv",
        "https://www.txsmartbuy.gov/esbd/csv?status=Posted",
    ]
    for url in csv_urls:
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ("csv" in ct or "text/plain" in ct or resp.text.count(",") > 50):
                return _parse_csv(resp.text)
        except Exception:
            pass
    return []


def _parse_csv(text: str) -> list[dict]:
    try:
        import csv, io
        reader = csv.DictReader(io.StringIO(text))
        items = []
        for row in reader:
            items.append({k.strip().lower(): v for k, v in row.items()})
        return items
    except Exception:
        return []


def fetch_html_pages(max_pages: int = 5) -> list[dict]:
    """Scrape the ESBD search results (status=Posted) page by page."""
    all_items = []
    for page in range(1, max_pages + 1):
        try:
            resp = httpx.get(
                BASE_URL,
                params={"status": "Posted", "page": page},
                headers=HEADERS, timeout=25, follow_redirects=True,
            )
            if resp.status_code != 200:
                print(f"[txsmartbuy] Page {page} → {resp.status_code}")
                break
            items = _parse_esbd_divs(resp.text, str(resp.url))
            if not items:
                print(f"[txsmartbuy] Page {page}: 0 bids parsed")
                break
            print(f"[txsmartbuy] Page {page}: {len(items)} bids")
            all_items.extend(items)
            if len(items) < 10:
                break  # last page
            time.sleep(0.5)
        except Exception as e:
            print(f"[txsmartbuy] Page {page} error: {e}")
            break
    return all_items


def _parse_esbd_divs(html: str, base_url: str) -> list[dict]:
    """
    Parse ESBD div-based bid listings.
    Structure observed:
      <a href="/esbd/...">TITLE OF BID</a>
      Solicitation ID: 405-26R0018465
      Due Date: 6/10/2026
      Agency/Texas SmartBuy Member Number: 405
      Status: Posted
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        domain = "https://www.txsmartbuy.gov"
        items = []

        # Find all links that point to ESBD detail pages
        for link in soup.find_all("a", href=True):
            href = link["href"]
            # ESBD detail links contain /esbd/ or similar path
            if not any(kw in href.lower() for kw in ["/esbd/", "bidId=", "solicitationId=", "SolId=", "/solicitation/"]):
                # Also accept if the parent section has ESBD-like content
                pass
            title = link.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            if not href.startswith("http"):
                href = f"{domain}{href}"

            # Get the surrounding context (parent div/section) for metadata
            parent = link.parent
            for _ in range(4):  # walk up to 4 levels
                if parent is None:
                    break
                parent_text = parent.get_text(" ")
                if "Solicitation ID" in parent_text or "Due Date" in parent_text:
                    break
                parent = parent.parent

            context = parent.get_text(" ") if parent else link.parent.get_text(" ")

            # Extract Solicitation ID
            sid_match = re.search(r"Solicitation ID[:\s]+([A-Z0-9\-]+)", context)
            bid_id = sid_match.group(1).strip() if sid_match else ""

            # Extract Due Date
            due_match = re.search(r"Due Date[:\s]+(\d{1,2}/\d{1,2}/\d{4})", context)
            due_date_raw = due_match.group(1) if due_match else None

            # Extract Agency Member Number
            agency_match = re.search(r"Member Number[:\s]+(\d+)", context)
            agency_num = agency_match.group(1) if agency_match else ""

            # Skip if status is not Posted
            if "Status" in context and "Posted" not in context:
                continue

            items.append({
                "id": bid_id or title[:40],
                "title": title,
                "url": href,
                "due_date": due_date_raw,
                "agency": f"TX Agency #{agency_num}" if agency_num else "Texas State Agency",
            })

        # Deduplicate by title
        seen = set()
        deduped = []
        for item in items:
            key = item["title"][:60]
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return deduped
    except Exception as e:
        print(f"[txsmartbuy] Div parse error: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    bid_id = item.get("id") or item.get("solicitation id") or item.get("solicitation_id") or ""
    title = (item.get("title") or item.get("solicitation title") or item.get("description") or "").strip()
    if not title or len(title) < 5:
        return None
    url = item.get("url") or BASE_URL
    due_date = None
    raw = item.get("due_date") or item.get("due date") or item.get("closing date") or ""
    if raw:
        try:
            raw_str = str(raw).strip()
            if "/" in raw_str:
                parts = raw_str.split("/")
                if len(parts) == 3:
                    m, d, y = parts
                    due_date = f"{y.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
            elif "T" in raw_str:
                due_date = raw_str[:10]
            else:
                due_date = raw_str[:10]
        except Exception:
            pass
    agency = item.get("agency") or item.get("agency/texas smartbuy member name") or "Texas State Agency"
    return {
        "source": "txsmartbuy",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(agency)[:300],
        "naics": str(item.get("naics") or item.get("class/item codes") or ""),
        "value": None,
        "due_date": due_date,
        "set_aside": "",
        "url": url,
        "raw_html": "",
    }


def upsert_contracts(contracts):
    if not contracts:
        return 0
    result = supabase.table("contracts").upsert(contracts, on_conflict="source,source_id", ignore_duplicates=True).execute()
    return len(result.data) if result.data else 0


def run():
    start = time.time()
    print("[txsmartbuy] Starting scrape...")
    items = fetch_csv()
    if items:
        print(f"[txsmartbuy] CSV export returned {len(items)} rows")
    else:
        items = fetch_html_pages(max_pages=5)
    print(f"[txsmartbuy] Total raw items: {len(items)}")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[txsmartbuy] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "txsmartbuy", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": None if items else "ESBD returned 0 — check URL or filter params",
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
