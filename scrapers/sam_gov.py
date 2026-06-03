"""
SAM.gov Opportunities Scraper
Uses the SAM.gov Public Opportunities API (no auth required for basic search,
API key used for higher rate limits).
"""

import os
import time
import httpx
from datetime import datetime, timedelta
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SAM_API_KEY = os.environ.get("SAM_API_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_URL = "https://api.sam.gov/opportunities/v2/search"
LIMIT = 100   # max per page
MAX_RECORDS = 500  # cap total per run for cost control


def fetch_opportunities(offset: int = 0) -> dict:
    """Fetch a page of SAM.gov opportunities for Texas."""
    posted_from = (datetime.now() - timedelta(days=30)).strftime("%m/%d/%Y")
    posted_to = datetime.now().strftime("%m/%d/%Y")

    # SAM.gov v2 API: use keyword "Texas" for broader TX coverage
    # pPlace filter is unreliable — we filter client-side on placeOfPerformance instead
    params = {
        "api_key": SAM_API_KEY,
        "limit": LIMIT,
        "offset": offset,
        "postedFrom": posted_from,
        "postedTo": posted_to,
        "q": "Texas",            # keyword search — broad TX coverage
        "active": "true",
    }

    if not SAM_API_KEY:
        print("[sam.gov] WARNING: SAM_API_KEY not set — requests will be rate-limited. Set the secret in GitHub Actions.")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = httpx.get(BASE_URL, params=params, timeout=30)

            if resp.status_code == 429:
                wait = 30 * (attempt + 1)  # shorter waits: 30s, 60s, 90s
                print(f"[sam.gov] Rate limited. Waiting {wait}s before retry {attempt + 1}/{max_retries}...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except httpx.HTTPStatusError as e:
            print(f"[sam.gov] HTTP error: {e}")
            if attempt < max_retries - 1:
                time.sleep(30)
            else:
                raise
        except Exception as e:
            print(f"[sam.gov] Request error: {e}")
            if attempt < max_retries - 1:
                time.sleep(15)
            else:
                raise

    return {}


def parse_opportunity(opp: dict) -> dict | None:
    """Extract fields from SAM.gov opportunity record. Returns None if not TX."""
    # Client-side TX filter as safety net
    pop = opp.get("placeOfPerformance") or {}
    pop_state = (pop.get("state") or {}).get("code") or ""
    if pop_state and pop_state.upper() not in ("TX", "TEXAS", ""):
        return None  # skip non-Texas contracts

    value = None
    award = opp.get("award") or {}
    if award.get("amount"):
        try:
            value = float(str(award["amount"]).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            pass

    # Try estimated value from description fields
    if value is None:
        for field in ["baseAndAllOptionsValue", "baseAndExercisedOptionsValue"]:
            if opp.get(field):
                try:
                    value = float(str(opp[field]).replace(",", "").replace("$", ""))
                    break
                except (ValueError, TypeError):
                    pass

    due_date = None
    for date_field in ["responseDeadLine", "archiveDate", "awardDate"]:
        if opp.get(date_field):
            try:
                due_date = str(opp[date_field])[:10]
                break
            except Exception:
                pass

    notice_id = opp.get("noticeId", "")
    url = f"https://sam.gov/opp/{notice_id}/view" if notice_id else "https://sam.gov/content/opportunities"

    return {
        "source": "sam_gov",
        "source_id": notice_id,
        "title": (opp.get("title") or "")[:500],
        "agency": (opp.get("fullParentPathName") or opp.get("departmentName") or "Federal Agency")[:300],
        "naics": opp.get("naicsCode") or "",
        "value": value,
        "due_date": due_date,
        "set_aside": opp.get("typeOfSetAside") or "",
        "url": url,
        "raw_html": str(opp.get("description") or "")[:5000],
    }


def upsert_contracts(contracts: list[dict]) -> int:
    """Upsert contracts to Supabase, return count of new records."""
    if not contracts:
        return 0

    result = supabase.table("contracts").upsert(
        contracts,
        on_conflict="source,source_id",
        ignore_duplicates=True,
    ).execute()

    return len(result.data) if result.data else 0


def run():
    start = time.time()
    print("[sam.gov] Starting scrape...")

    total_found = 0
    total_new = 0
    offset = 0

    while offset < MAX_RECORDS:
        print(f"[sam.gov] Fetching offset {offset}...")

        try:
            data = fetch_opportunities(offset)
        except Exception as e:
            print(f"[sam.gov] FAILED at offset {offset}: {e}")
            break

        opportunities = data.get("opportunitiesData", [])
        if not opportunities:
            print("[sam.gov] No more results.")
            break

        total_found += len(opportunities)

        contracts = []
        for opp in opportunities:
            parsed = parse_opportunity(opp)
            if parsed and parsed["source_id"]:
                contracts.append(parsed)

        new_count = upsert_contracts(contracts)
        total_new += new_count
        print(f"[sam.gov] Batch {offset}–{offset + len(opportunities)}: {new_count} new")

        if len(opportunities) < LIMIT:
            break  # last page

        offset += LIMIT
        time.sleep(2)  # polite delay between pages

    duration = int((time.time() - start) * 1000)
    print(f"[sam.gov] Done. Found: {total_found}, New: {total_new}")

    supabase.table("scraper_logs").insert({
        "source": "sam_gov",
        "status": "success",
        "contracts_found": total_found,
        "contracts_new": total_new,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
