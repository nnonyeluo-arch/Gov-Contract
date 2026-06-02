"""
TxDOT Procurement Scraper
Texas Department of Transportation posts construction, engineering,
and services contracts via their LPA/CMS portal and SAM.gov.
TxDOT is one of the largest TX government spenders.
"""

import os
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
}

# TxDOT Let List API — construction contract lettings (public)
TXDOT_LET_API = "https://www.txdot.gov/business/let-list/api/lettings"
TXDOT_LET_PAGE = "https://www.txdot.gov/business/let-list.html"

# TxDOT Professional Services / Purchasing
TXDOT_PURCHASE_ENDPOINTS = [
    "https://www.txdot.gov/business/purchasing/api/solicitations",
    "https://fmcpa.cpa.state.tx.us/esbd/content/pubMain.do",
]

# TxDOT open data API
TXDOT_OPEN_DATA = "https://data.txdot.gov/resource/chhm-xrjn.json"


def fetch_let_list() -> list[dict]:
    """Fetch TxDOT construction lettings (bid openings for highway contracts)."""
    try:
        resp = httpx.get(
            TXDOT_LET_API,
            params={"status": "upcoming", "limit": 100},
            headers=HEADERS,
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            try:
                data = resp.json()
                items = data.get("lettings") or data.get("data") or data.get("results") or []
                if items:
                    print(f"[txdot] Let List API returned {len(items)} items")
                    return items
            except Exception:
                pass
    except Exception as e:
        print(f"[txdot] Let List API error: {e}")

    # Try scraping the let list page
    try:
        resp = httpx.get(TXDOT_LET_PAGE, headers={**HEADERS, "Accept": "text/html"}, timeout=20, follow_redirects=True)
        if resp.status_code == 200:
            return _parse_let_list_html(resp.text)
    except Exception as e:
        print(f"[txdot] Let List page error: {e}")
    return []


def _parse_let_list_html(html: str) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(html, "html.parser")
        items = []
        for row in soup.select("table tr, .letting-row, tr.data-row"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link = row.find("a", href=True)
            title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            if not title or title.lower() in ("description", "project"):
                continue
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://www.txdot.gov{href}"
            all_text = row.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            # Look for dollar amounts
            amounts = re.findall(r"\$[\d,]+(?:\.\d+)?", all_text)
            value = None
            if amounts:
                try:
                    value = float(amounts[0].replace("$", "").replace(",", ""))
                except Exception:
                    pass
            items.append({
                "id": title[:40],
                "title": title,
                "url": href or TXDOT_LET_PAGE,
                "due_date": dates[0] if dates else None,
                "value": value,
                "department": "TxDOT",
            })
        return items
    except Exception as e:
        print(f"[txdot] HTML parse error: {e}")
        return []


def fetch_open_data() -> list[dict]:
    """Fallback: TxDOT open data contracts."""
    try:
        resp = httpx.get(
            TXDOT_OPEN_DATA,
            params={"$limit": 200, "$order": "letting_date DESC", "$where": "status='Active'"},
            headers={**HEADERS, "Accept": "application/json"},
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                print(f"[txdot] Open Data returned {len(data)} records")
                return data
    except Exception as e:
        print(f"[txdot] Open Data error: {e}")
    return []


def parse_item(item: dict) -> dict | None:
    bid_id = (
        item.get("control_section") or item.get("id") or item.get("project_id")
        or item.get("contract_id") or item.get("letting_id") or ""
    )
    title = (
        item.get("description") or item.get("title") or item.get("project_description")
        or item.get("work_type") or ""
    ).strip()
    if not title:
        return None

    url = item.get("url") or item.get("link") or TXDOT_LET_PAGE

    due_date = None
    for field in ["letting_date", "due_date", "bid_date", "closing_date", "open_date"]:
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

    value = item.get("value") or item.get("estimated_cost") or item.get("low_bid") or item.get("amount")
    if value:
        try:
            value = float(str(value).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            value = None

    county = item.get("county") or item.get("location") or ""
    agency = f"TxDOT{' — ' + county if county else ''}"

    return {
        "source": "txdot",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": agency[:300],
        "naics": "237310",  # Highway/Street/Bridge Construction NAICS
        "value": value,
        "due_date": due_date,
        "set_aside": "",
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
    print("[txdot] Starting scrape...")
    items = fetch_let_list()
    if not items:
        print("[txdot] Let list returned nothing, trying Open Data...")
        items = fetch_open_data()
    print(f"[txdot] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[txdot] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "txdot", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": "All endpoints returned 0" if not items else None,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
