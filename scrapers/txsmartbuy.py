"""
Scraper: Texas SmartBuy (txsmartbuy.gov)
Runs daily via GitHub Actions cron at 6am CT
"""

import os
import hashlib
import time
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

BASE_URL = "https://www.txsmartbuy.gov"
SEARCH_URL = f"{BASE_URL}/esbd"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def log_run(status: str, found: int = 0, new: int = 0, error: str = None, duration_ms: int = 0):
    supabase.table("scraper_logs").insert({
        "source": "txsmartbuy",
        "status": status,
        "contracts_found": found,
        "contracts_new": new,
        "error_message": error,
        "duration_ms": duration_ms,
    }).execute()


def parse_value(value_str: str) -> float | None:
    if not value_str:
        return None
    cleaned = value_str.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(date_str: str) -> str | None:
    if not date_str:
        return None
    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]:
        try:
            return datetime.strptime(date_str.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def scrape_listing_page(client: httpx.Client, page: int = 1) -> list[dict]:
    """Fetch one page of active bids from TX SmartBuy ESBD."""
    params = {
        "status": "OPEN",
        "page": page,
    }
    try:
        resp = client.get(SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[txsmartbuy] HTTP error on page {page}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("table.esbd-results tbody tr")

    contracts = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        link_tag = cells[0].find("a")
        title = link_tag.get_text(strip=True) if link_tag else cells[0].get_text(strip=True)
        relative_url = link_tag.get("href", "") if link_tag else ""
        url = f"{BASE_URL}{relative_url}" if relative_url.startswith("/") else relative_url

        # Extract source_id from URL or title
        source_id = relative_url.split("/")[-1] if relative_url else hashlib.md5(title.encode()).hexdigest()[:16]

        contracts.append({
            "source": "txsmartbuy",
            "source_id": source_id,
            "title": title,
            "agency": cells[1].get_text(strip=True) if len(cells) > 1 else None,
            "value": parse_value(cells[2].get_text(strip=True)) if len(cells) > 2 else None,
            "due_date": parse_date(cells[3].get_text(strip=True)) if len(cells) > 3 else None,
            "set_aside": cells[4].get_text(strip=True) if len(cells) > 4 else None,
            "url": url,
            "raw_html": str(row),
        })

    return contracts


def upsert_contracts(contracts: list[dict]) -> int:
    """Insert new contracts, skip duplicates. Returns count of new rows."""
    if not contracts:
        return 0

    new_count = 0
    for contract in contracts:
        result = supabase.table("contracts").upsert(
            contract,
            on_conflict="source,source_id",
            ignore_duplicates=True
        ).execute()
        if result.data:
            new_count += len(result.data)

    return new_count


def run():
    start = time.time()
    print("[txsmartbuy] Starting scrape...")

    all_contracts = []
    try:
        with httpx.Client(headers={"User-Agent": "Mozilla/5.0 GovContractBot/1.0"}) as client:
            # Scrape first 5 pages (50 results typically per page)
            for page in range(1, 6):
                contracts = scrape_listing_page(client, page)
                if not contracts:
                    break
                all_contracts.extend(contracts)
                print(f"[txsmartbuy] Page {page}: {len(contracts)} contracts")
                time.sleep(1)  # be polite

        new_count = upsert_contracts(all_contracts)
        duration = int((time.time() - start) * 1000)

        print(f"[txsmartbuy] Done. Found: {len(all_contracts)}, New: {new_count}")
        log_run("success", found=len(all_contracts), new=new_count, duration_ms=duration)

    except Exception as e:
        duration = int((time.time() - start) * 1000)
        print(f"[txsmartbuy] ERROR: {e}")
        log_run("error", error=str(e), duration_ms=duration)
        raise


if __name__ == "__main__":
    run()
