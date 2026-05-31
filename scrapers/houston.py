"""
Scraper: City of Houston Procurement
https://purchasing.houstontx.gov/
Runs daily via GitHub Actions cron at 6am CT
"""

import os
import time
import hashlib
import httpx
from bs4 import BeautifulSoup
from datetime import datetime
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

BASE_URL = "https://purchasing.houstontx.gov"
BID_LIST_URL = f"{BASE_URL}/biddetail.aspx"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def log_run(status, found=0, new=0, error=None, duration_ms=0):
    supabase.table("scraper_logs").insert({
        "source": "houston",
        "status": status,
        "contracts_found": found,
        "contracts_new": new,
        "error_message": error,
        "duration_ms": duration_ms,
    }).execute()


def parse_date(date_str):
    if not date_str:
        return None
    for fmt in ["%m/%d/%Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(date_str.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def scrape(client: httpx.Client) -> list[dict]:
    try:
        resp = client.get(BID_LIST_URL, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[houston] HTTP error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    contracts = []

    # Houston procurement typically uses a table or grid layout
    rows = soup.select("table tr") or soup.select(".bid-item")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        link = cells[0].find("a")
        title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
        href = link.get("href", "") if link else ""
        url = f"{BASE_URL}/{href}" if href and not href.startswith("http") else href

        source_id = href.split("=")[-1] if "=" in href else hashlib.md5(title.encode()).hexdigest()[:16]

        contracts.append({
            "source": "houston",
            "source_id": source_id,
            "title": title,
            "agency": "City of Houston",
            "due_date": parse_date(cells[2].get_text(strip=True)) if len(cells) > 2 else None,
            "url": url or f"{BASE_URL}/biddetail.aspx",
            "raw_html": str(row),
        })

    return contracts


def upsert_contracts(contracts):
    if not contracts:
        return 0
    new_count = 0
    for c in contracts:
        result = supabase.table("contracts").upsert(
            c, on_conflict="source,source_id", ignore_duplicates=True
        ).execute()
        if result.data:
            new_count += len(result.data)
    return new_count


def run():
    start = time.time()
    print("[houston] Starting scrape...")
    try:
        with httpx.Client(headers={"User-Agent": "Mozilla/5.0 GovContractBot/1.0"}) as client:
            contracts = scrape(client)
        new_count = upsert_contracts(contracts)
        duration = int((time.time() - start) * 1000)
        print(f"[houston] Done. Found: {len(contracts)}, New: {new_count}")
        log_run("success", found=len(contracts), new=new_count, duration_ms=duration)
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        print(f"[houston] ERROR: {e}")
        log_run("error", error=str(e), duration_ms=duration)
        raise


if __name__ == "__main__":
    run()
