"""
City of Houston Procurement Scraper
Houston uses Beacon (beaconbid.com) for solicitations.
Tries multiple Beacon API endpoint patterns + Houston Open Data fallback.
"""

import os
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ORG_SLUG = "city-of-houston"
BEACON_VIEW_URL = f"https://www.beaconbid.com/solicitations/{ORG_SLUG}/open"

# Beacon API endpoint patterns to try (they change these periodically)
BEACON_ENDPOINTS = [
    f"https://www.beaconbid.com/api/organizations/{ORG_SLUG}/solicitations",
    f"https://www.beaconbid.com/api/v1/organizations/{ORG_SLUG}/solicitations",
    f"https://www.beaconbid.com/api/solicitations",
    f"https://www.beaconbid.com/api/v1/solicitations",
    f"https://www.beaconbid.com/api/public/solicitations",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.houstontx.gov/bizwithhou/",
    "Origin": "https://www.houstontx.gov",
}

# Houston Open Data – archived/current solicitations dataset IDs to try
OPEN_DATA_DATASETS = [
    "https://data.houstontx.gov/resource/i4j7-5rp6.json",
    "https://data.houstontx.gov/resource/wv4c-gv4e.json",
    "https://data.houstontx.gov/resource/mvtx-nrn7.json",
]


def fetch_beacon() -> list[dict]:
    """Try multiple Beacon API endpoints for Houston solicitations."""
    for endpoint in BEACON_ENDPOINTS:
        all_items = []
        for page in range(1, 6):
            try:
                params = {"status": "open", "per_page": 100, "page": page}
                # Also try with org slug as param
                if "solicitations" == endpoint.split("/")[-1] and ORG_SLUG not in endpoint:
                    params["organization"] = ORG_SLUG
                    params["organization_slug"] = ORG_SLUG

                resp = httpx.get(endpoint, params=params, headers=HEADERS, timeout=20, follow_redirects=True)

                if resp.status_code in (401, 403, 404, 405, 422):
                    break
                if resp.status_code != 200:
                    break

                try:
                    data = resp.json()
                except Exception:
                    break

                items = (
                    data.get("solicitations") or data.get("data") or
                    data.get("results") or data.get("bids") or []
                )
                if not items:
                    break

                all_items.extend(items)
                print(f"[houston] Beacon {endpoint} page {page}: {len(items)} items")

                if len(items) < 100:
                    break
                time.sleep(1)

            except Exception as e:
                print(f"[houston] Beacon {endpoint} page {page} error: {e}")
                break

        if all_items:
            print(f"[houston] Beacon success: {len(all_items)} total from {endpoint}")
            return all_items

    print("[houston] All Beacon endpoints returned nothing")
    return []


def fetch_open_data() -> list[dict]:
    """Fallback: Houston Open Data portal."""
    for url in OPEN_DATA_DATASETS:
        try:
            resp = httpx.get(
                url,
                params={"$limit": 200, "$order": "posted_date DESC"},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=20,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    print(f"[houston] Open Data returned {len(data)} records from {url}")
                    return data
        except Exception as e:
            print(f"[houston] Open Data {url} failed: {e}")
    return []


def parse_beacon_item(item: dict) -> dict | None:
    """Parse a Beacon solicitation record."""
    sol_id = (
        item.get("id") or item.get("solicitation_number") or item.get("number")
        or item.get("bid_number") or ""
    )
    title = item.get("title") or item.get("name") or ""

    if not sol_id or not title:
        return None

    value = None
    for field in ["estimated_value", "award_amount", "budget", "amount"]:
        if item.get(field):
            try:
                value = float(str(item[field]).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass

    due_date = None
    for field in ["due_date", "response_deadline", "close_date", "closing_date", "dueDate"]:
        if item.get(field):
            try:
                raw = str(item[field])
                if "T" in raw:
                    due_date = raw[:10]
                elif "/" in raw:
                    parts = raw[:10].split("/")
                    if len(parts) == 3:
                        m, d, y = parts
                        due_date = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                else:
                    due_date = raw[:10]
                break
            except Exception:
                pass

    url = item.get("url") or item.get("link") or ""
    if not url and sol_id:
        url = f"https://www.beaconbid.com/solicitations/{ORG_SLUG}/{sol_id}"
    if not url:
        url = BEACON_VIEW_URL

    return {
        "source": "houston",
        "source_id": str(sol_id),
        "title": str(title)[:500],
        "agency": "City of Houston",
        "naics": item.get("naics_code") or "",
        "value": value,
        "due_date": due_date,
        "set_aside": item.get("set_aside") or "",
        "url": url,
        "raw_html": str(item.get("description") or item.get("scope") or "")[:5000],
    }


def parse_open_data_item(item: dict) -> dict | None:
    """Parse a Houston Open Data record."""
    sol_id = item.get("bid_number") or item.get("solicitation_number") or item.get("id") or ""
    title = item.get("title") or item.get("description") or item.get("bid_title") or ""

    if not title:
        return None

    due_date = None
    for field in ["due_date", "bid_due_date", "closing_date", "posted_date"]:
        raw = item.get(field)
        if raw:
            try:
                raw_str = str(raw)
                if "T" in raw_str:
                    due_date = raw_str[:10]
                elif "/" in raw_str:
                    parts = raw_str[:10].split("/")
                    if len(parts) == 3:
                        m, d, y = parts
                        due_date = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                else:
                    due_date = raw_str[:10]
                break
            except Exception:
                pass

    return {
        "source": "houston",
        "source_id": str(sol_id or title[:60]),
        "title": str(title)[:500],
        "agency": "City of Houston",
        "naics": "",
        "value": None,
        "due_date": due_date,
        "set_aside": "",
        "url": item.get("url") or BEACON_VIEW_URL,
        "raw_html": "",
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

    # Try Beacon first
    raw_items = fetch_beacon()
    parser = parse_beacon_item

    # Fallback to Open Data
    if not raw_items:
        print("[houston] Beacon returned nothing, trying Open Data fallback...")
        raw_items = fetch_open_data()
        parser = parse_open_data_item

    print(f"[houston] Found {len(raw_items)} raw items")

    contracts = []
    for item in raw_items:
        parsed = parser(item)
        if parsed:
            contracts.append(parsed)

    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[houston] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")

    supabase.table("scraper_logs").insert({
        "source": "houston",
        "status": "success" if len(raw_items) > 0 else "empty",
        "contracts_found": len(raw_items),
        "contracts_new": new_count,
        "error_message": "All endpoints returned 0 — may need manual URL check" if not raw_items else None,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
