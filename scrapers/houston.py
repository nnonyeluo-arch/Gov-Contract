"""
City of Houston Procurement Scraper
Houston uses Beacon (beaconbid.com) — a JS-rendered React SPA.
The underlying API is: https://www.beaconbid.com/api/ggf?operation=ListSolicitations
(Confirmed via DevTools network intercept — all calls go to /api/ggf with operation param.)
Strategy:
  1. Direct API call to Beacon's /api/ggf endpoint (no auth required, same-origin CORS but works server-side)
  2. Fallback: Houston Open Data (Socrata)
"""

import os
import re
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ORG_SLUG = "city-of-houston"
BEACON_URL = f"https://www.beaconbid.com/solicitations/{ORG_SLUG}/open"
BEACON_API = "https://www.beaconbid.com/api/ggf"
HOUSTON_OPEN_DATA_URLS = [
    "https://data.houstontx.gov/resource/i4j7-5rp6.json",
    "https://data.houstontx.gov/resource/wv4c-gv4e.json",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Referer": BEACON_URL,
    "Origin": "https://www.beaconbid.com",
}


def fetch_beacon_api() -> list[dict]:
    """
    Call Beacon's internal GGF API directly.
    The operation=ListSolicitations endpoint returns the open bids for the agency.
    Try several parameter combinations to find what works for Houston.
    """
    print("[houston] Trying Beacon API direct call...")

    # Possible parameter shapes for ListSolicitations
    param_variants = [
        {"operation": "ListSolicitations", "orgSlug": ORG_SLUG, "status": "open"},
        {"operation": "ListSolicitations", "agency": ORG_SLUG, "status": "open"},
        {"operation": "ListSolicitations", "slug": ORG_SLUG},
        {"operation": "ListSolicitations", "orgSlug": ORG_SLUG},
        {"operation": "ListSolicitations"},
    ]

    for params in param_variants:
        try:
            resp = httpx.get(BEACON_API, params=params, headers=HEADERS, timeout=20, follow_redirects=True)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    # API may return list directly or nested under a key
                    if isinstance(data, list) and data:
                        print(f"[houston] Beacon API returned {len(data)} items (params: {params})")
                        return data
                    if isinstance(data, dict):
                        for key in ["solicitations", "data", "results", "items", "bids"]:
                            if isinstance(data.get(key), list) and data[key]:
                                print(f"[houston] Beacon API returned {len(data[key])} items under '{key}'")
                                return data[key]
                except Exception:
                    pass
            print(f"[houston] Beacon API params {params} → {resp.status_code}")
        except Exception as e:
            print(f"[houston] Beacon API error ({params}): {e}")

    # Try POST variant
    try:
        resp = httpx.post(
            BEACON_API,
            json={"operation": "ListSolicitations", "orgSlug": ORG_SLUG, "status": "open"},
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return data
            if isinstance(data, dict):
                for key in ["solicitations", "data", "results", "items", "bids"]:
                    if isinstance(data.get(key), list) and data[key]:
                        return data[key]
    except Exception as e:
        print(f"[houston] Beacon API POST error: {e}")

    return []


def fetch_open_data() -> list[dict]:
    """Fallback: Houston Open Data (Socrata)."""
    for url in HOUSTON_OPEN_DATA_URLS:
        try:
            resp = httpx.get(
                url,
                params={"$limit": 200, "$order": "close_date DESC"},
                headers=HEADERS,
                timeout=20,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    print(f"[houston] Open Data returned {len(data)} records from {url}")
                    return data
        except Exception as e:
            print(f"[houston] Open Data {url} error: {e}")
    return []


def parse_item(item: dict) -> dict | None:
    bid_id = (
        item.get("id") or item.get("bid_id") or item.get("solicitation_number")
        or item.get("number") or item.get("solicitation_id") or ""
    )
    title = (
        item.get("title") or item.get("name") or item.get("description")
        or item.get("solicitation_name") or ""
    ).strip() if isinstance(
        item.get("title") or item.get("name") or item.get("description") or item.get("solicitation_name") or "", str
    ) else ""
    if not title:
        return None

    url = item.get("url") or item.get("link") or item.get("detail_url") or BEACON_URL

    due_date = None
    for field in ["due_date", "closing_date", "close_date", "dueDate", "closeDate", "response_deadline"]:
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
    for field in ["estimated_value", "value", "amount", "budget", "total_value"]:
        raw = item.get(field)
        if raw:
            try:
                value = float(str(raw).replace(",", "").replace("$", "").strip())
                break
            except (ValueError, TypeError):
                pass

    return {
        "source": "houston",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(item.get("department") or item.get("agency") or "City of Houston")[:300],
        "naics": str(item.get("naics") or item.get("naics_code") or ""),
        "value": value,
        "due_date": due_date,
        "set_aside": str(item.get("set_aside") or ""),
        "url": url,
        "raw_html": str(item.get("description") or item.get("scope") or "")[:5000],
    }


def upsert_contracts(contracts):
    if not contracts:
        return 0
    result = supabase.table("contracts").upsert(contracts, on_conflict="source,source_id", ignore_duplicates=True).execute()
    return len(result.data) if result.data else 0


def run():
    start = time.time()
    print("[houston] Starting scrape...")
    items = fetch_beacon_api()
    if not items:
        print("[houston] Beacon API returned nothing, trying Open Data fallback...")
        items = fetch_open_data()
    print(f"[houston] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[houston] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "houston", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": None if items else "All Houston endpoints returned 0",
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
