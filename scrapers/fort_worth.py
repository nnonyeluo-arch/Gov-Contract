"""
City of Fort Worth Procurement Scraper
Fort Worth uses Bonfire (bonfirehub.com) for solicitations.
"""

import os
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ORG_SLUG = "fortworthtx"
BONFIRE_VIEW = f"https://{ORG_SLUG}.bonfirehub.com/portal/?tab=openOpportunities"

BONFIRE_ENDPOINTS = [
    f"https://{ORG_SLUG}.bonfirehub.com/portal/api/opportunities",
    f"https://{ORG_SLUG}.bonfirehub.com/api/v2/opportunities",
    f"https://{ORG_SLUG}.bonfirehub.com/api/opportunities",
    "https://fortworthtx.bonfirehub.com/portal/api/public/opportunities",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Referer": BONFIRE_VIEW,
}


def fetch_bonfire() -> list[dict]:
    all_items = []
    for endpoint in BONFIRE_ENDPOINTS:
        for page in range(1, 6):
            try:
                resp = httpx.get(
                    endpoint,
                    params={"status": "open", "page": page, "per_page": 100},
                    headers=HEADERS,
                    timeout=20,
                    follow_redirects=True,
                )
                if resp.status_code in (400, 401, 403, 404, 405):
                    break
                if resp.status_code != 200:
                    break
                try:
                    data = resp.json()
                except Exception:
                    break
                items = data.get("opportunities") or data.get("data") or data.get("results") or []
                if not items:
                    break
                all_items.extend(items)
                if len(items) < 100:
                    break
                time.sleep(1)
            except Exception as e:
                print(f"[fort_worth] {endpoint} error: {e}")
                break
        if all_items:
            print(f"[fort_worth] Got {len(all_items)} items from {endpoint}")
            return all_items
    return all_items


def fetch_html_fallback() -> list[dict]:
    """Scrape Fort Worth purchasing page."""
    try:
        resp = httpx.get(
            "https://www.fortworthtexas.gov/departments/finance/purchasing",
            headers={**HEADERS, "Accept": "text/html"},
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(resp.text, "html.parser")
        items = []
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link = row.find("a", href=True)
            title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            if not title:
                continue
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://www.fortworthtexas.gov{href}"
            all_text = row.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            items.append({"id": title[:40], "title": title, "url": href, "due_date": dates[-1] if dates else None})
        return items
    except Exception as e:
        print(f"[fort_worth] HTML fallback error: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    bid_id = (item.get("number") or item.get("id") or item.get("opportunity_id") or item.get("solicitation_number") or "")
    title = (item.get("description") or item.get("title") or item.get("name") or "").strip()
    if not title:
        return None

    url = item.get("url") or item.get("link") or ""
    if not url and bid_id:
        url = f"{BONFIRE_VIEW}&bidId={bid_id}"
    if not url:
        url = BONFIRE_VIEW

    due_date = None
    for field in ["closingdate", "due_date", "closing_date", "close_date", "response_deadline", "dueDate"]:
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

    value = None
    for field in ["estimated_value", "value", "amount", "budget"]:
        raw = item.get(field)
        if raw:
            try:
                value = float(str(raw).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass

    return {
        "source": "fort_worth",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": item.get("department") or "City of Fort Worth",
        "naics": item.get("naics") or "",
        "value": value,
        "due_date": due_date,
        "set_aside": item.get("set_aside") or "",
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
    print("[fort_worth] Starting scrape...")
    items = fetch_bonfire()
    if not items:
        print("[fort_worth] Bonfire returned nothing, trying HTML fallback...")
        items = fetch_html_fallback()
    print(f"[fort_worth] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[fort_worth] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "fort_worth", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": "All endpoints returned 0" if not items else None,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
