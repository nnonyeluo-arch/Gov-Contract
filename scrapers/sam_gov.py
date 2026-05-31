"""
Scraper: SAM.gov — Texas-filtered federal contracts
Uses the SAM.gov Public API (free, requires API key)
Runs daily via GitHub Actions cron at 6am CT
"""

import os
import time
import hashlib
import httpx
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SAM_API_KEY  = os.environ["SAM_API_KEY"]

SAM_BASE_URL = "https://api.sam.gov/opportunities/v2/search"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def log_run(status: str, found: int = 0, new: int = 0, error: str = None, duration_ms: int = 0):
    supabase.table("scraper_logs").insert({
        "source": "sam_gov",
        "status": status,
        "contracts_found": found,
        "contracts_new": new,
        "error_message": error,
        "duration_ms": duration_ms,
    }).execute()


def fetch_opportunities(client: httpx.Client, offset: int = 0) -> dict:
    """Fetch one page of Texas opportunities from SAM.gov API."""
    # Look back 30 days for new postings
    posted_from = (datetime.now() - timedelta(days=30)).strftime("%m/%d/%Y")
    posted_to   = datetime.now().strftime("%m/%d/%Y")

    params = {
        "api_key": SAM_API_KEY,
        "postedFrom": posted_from,
        "postedTo": posted_to,
        "state": "TX",
        "limit": 100,
        "offset": offset,
        "active": "true",
    }

    resp = client.get(SAM_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def map_opportunity(opp: dict) -> dict:
    """Map SAM.gov opportunity to our contracts schema."""
    source_id = opp.get("noticeId") or hashlib.md5(opp.get("title", "").encode()).hexdigest()[:16]

    # Parse due date
    due_date = None
    response_deadline = opp.get("responseDeadLine")
    if response_deadline:
        try:
            due_date = datetime.strptime(response_deadline[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass

    # Parse value
    award = opp.get("award", {}) or {}
    value = None
    if award.get("amount"):
        try:
            value = float(str(award["amount"]).replace(",", ""))
        except (ValueError, TypeError):
            pass

    return {
        "source": "sam_gov",
        "source_id": source_id,
        "title": opp.get("title", "Untitled"),
        "agency": opp.get("department") or opp.get("subtier"),
        "naics": opp.get("naicsCode"),
        "value": value,
        "due_date": due_date,
        "set_aside": opp.get("typeOfSetAsideDescription"),
        "url": f"https://sam.gov/opp/{source_id}/view",
        "raw_html": str(opp),
    }


def upsert_contracts(contracts: list[dict]) -> int:
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
    print("[sam_gov] Starting scrape...")

    all_contracts = []
    try:
        with httpx.Client() as client:
            offset = 0
            while True:
                data = fetch_opportunities(client, offset)
                opps = data.get("opportunitiesData", [])

                if not opps:
                    break

                mapped = [map_opportunity(o) for o in opps]
                all_contracts.extend(mapped)
                print(f"[sam_gov] Fetched {len(opps)} at offset {offset}")

                total = data.get("totalRecords", 0)
                offset += len(opps)
                if offset >= total or offset >= 500:  # cap at 500 for cost control
                    break

                time.sleep(0.5)

        new_count = upsert_contracts(all_contracts)
        duration = int((time.time() - start) * 1000)

        print(f"[sam_gov] Done. Found: {len(all_contracts)}, New: {new_count}")
        log_run("success", found=len(all_contracts), new=new_count, duration_ms=duration)

    except Exception as e:
        duration = int((time.time() - start) * 1000)
        print(f"[sam_gov] ERROR: {e}")
        log_run("error", error=str(e), duration_ms=duration)
        raise


if __name__ == "__main__":
    run()
