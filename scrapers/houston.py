"""
City of Houston Procurement Scraper
Houston moved to Beacon (beaconbid.com) for solicitations.
We use the Beacon public widget API endpoint.
"""

import os
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Beacon public API for City of Houston solicitations
BEACON_API = "https://www.beaconbid.com/api/solicitations"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GovContractIntelBot/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.houstontx.gov/bizwithhou/",
}


def fetch_solicitations() -> list[dict]:
    """Fetch open solicitations from Beacon for City of Houston."""
    params = {
        "organization": "city-of-houston",
        "status": "open",
        "per_page": 100,
        "page": 1,
    }

    all_items = []

    for page in range(1, 6):  # max 5 pages
        params["page"] = page
        try:
            resp = httpx.get(BEACON_API, params=params, headers=HEADERS, timeout=20, follow_redirects=True)

            if resp.status_code == 403 or resp.status_code == 404:
                print(f"[houston] Beacon API returned {resp.status_code} — trying fallback URL")
                break

            resp.raise_for_status()
            data = resp.json()

            items = data.get("solicitations") or data.get("data") or data.get("results") or []
            if not items:
                break

            all_items.extend(items)

            if len(items) < 100:
                break  # last page

            time.sleep(1)

        except httpx.HTTPStatusError as e:
            print(f"[houston] HTTP error page {page}: {e}")
            break
        except Exception as e:
            print(f"[houston] Error fetching page {page}: {e}")
            break

    return all_items


def fetch_solicitations_rss() -> list[dict]:
    """Fallback: fetch via Houston Open Data portal."""
    # Houston posts some procurement data on data.houstontx.gov
    url = "https://data.houstontx.gov/resource/i4j7-5rp6.json"
    params = {"$limit": 100, "$order": "posted_date DESC"}

    try:
        resp = httpx.get(url, params=params, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[houston] Open Data fallback also failed: {e}")
        return []


def parse_beacon_item(item: dict) -> dict | None:
    """Parse a Beacon solicitation record."""
    sol_id = item.get("id") or item.get("solicitation_number") or item.get("number")
    title = item.get("title") or item.get("name") or ""

    if not sol_id or not title:
        return None

    value = None
    for field in ["estimated_value", "award_amount", "budget"]:
        if item.get(field):
            try:
                value = float(str(item[field]).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass

    due_date = None
    for field in ["due_date", "response_deadline", "close_date"]:
        if item.get(field):
            try:
                due_date = str(item[field])[:10]
                break
            except Exception:
                pass

    return {
        "source": "houston",
        "source_id": str(sol_id),
        "title": str(title)[:500],
        "agency": "City of Houston",
        "naics": item.get("naics_code") or "",
        "value": value,
        "due_date": due_date,
        "set_aside": item.get("set_aside") or "",
        "url": item.get("url") or item.get("link") or f"https://www.beaconbid.com/solicitations/city-of-houston/open",
        "raw_html": str(item.get("description") or item.get("scope") or "")[:5000],
    }


def upsert_contracts(contracts: list[dict]) -> int:
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
    print("[houston] Starting scrape...")

    items = fetch_solicitations()

    if not items:
        print("[houston] Beacon API returned no results, trying fallback...")
        items = fetch_solicitations_rss()

    print(f"[houston] Found {len(items)} raw items")

    contracts = []
    for item in items:
        parsed = parse_beacon_item(item)
        if parsed:
            contracts.append(parsed)

    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)

    print(f"[houston] Done. Found: {len(items)}, New: {new_count}")

    supabase.table("scraper_logs").insert({
        "source": "houston",
        "status": "success" if len(items) >= 0 else "partial",
        "contracts_found": len(items),
        "contracts_new": new_count,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
