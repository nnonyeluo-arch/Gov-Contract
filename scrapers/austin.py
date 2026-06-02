"""
City of Austin Procurement Scraper
Austin uses the Bonfire procurement platform for solicitations.
Falls back to the Austin Open Data portal.
"""

import os
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TXContractIntelBot/1.0)",
    "Accept": "application/json",
}

# Bonfire platform — City of Austin
BONFIRE_API = "https://austintexas.bonfirehub.com/api/opportunities"
# Austin Open Data fallback
AUSTIN_OPEN_DATA = "https://data.austintexas.gov/resource/q5dm-4f5i.json"


def fetch_bonfire() -> list[dict]:
    """Fetch open solicitations from Bonfire (Austin's procurement platform)."""
    all_items = []
    for page in range(1, 6):
        try:
            resp = httpx.get(
                BONFIRE_API,
                params={"status": "open", "page": page, "per_page": 100},
                headers=HEADERS,
                timeout=20,
                follow_redirects=True,
            )
            if resp.status_code in (403, 404):
                print(f"[austin] Bonfire API returned {resp.status_code}")
                break
            resp.raise_for_status()
            data = resp.json()
            items = data.get("opportunities") or data.get("data") or data.get("results") or []
            if not items:
                break
            all_items.extend(items)
            if len(items) < 100:
                break
            time.sleep(1)
        except Exception as e:
            print(f"[austin] Bonfire error page {page}: {e}")
            break
    return all_items


def fetch_open_data() -> list[dict]:
    """Fallback: Austin Open Data procurement dataset."""
    try:
        resp = httpx.get(
            AUSTIN_OPEN_DATA,
            params={"$limit": 200, "$order": "date_issued DESC", "$where": "status='Open'"},
            headers=HEADERS,
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[austin] Open Data fallback failed: {e}")
        return []


def fetch_solicitations_scrape() -> list[dict]:
    """Last resort: scrape the Austin purchasing page."""
    try:
        resp = httpx.get(
            "https://www.austintexas.gov/department/solicitations",
            headers={**HEADERS, "Accept": "text/html"},
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(resp.text, "html.parser")
        items = []
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link_tag = row.find("a", href=True)
            title = link_tag.get_text(strip=True) if link_tag else cells[0].get_text(strip=True)
            href = link_tag["href"] if link_tag else ""
            if href and not href.startswith("http"):
                href = f"https://www.austintexas.gov{href}"
            all_text = row.get_text(" ")
            date_matches = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            bid_id = ""
            for cell in cells:
                text = cell.get_text(strip=True)
                if re.match(r"[A-Z]{2,4}[-\d]+", text):
                    bid_id = text
                    break
            if title:
                items.append({
                    "id": bid_id or title[:40],
                    "title": title,
                    "url": href,
                    "due_date": date_matches[-1] if date_matches else None,
                })
        return items
    except Exception as e:
        print(f"[austin] Scrape fallback failed: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    """Normalize a record into our contracts schema."""
    bid_id = (
        item.get("id") or item.get("opportunity_id") or item.get("solicitation_number")
        or item.get("bid_number") or ""
    )
    title = (item.get("title") or item.get("name") or item.get("description") or "").strip()
    if not title:
        return None

    url = item.get("url") or item.get("link") or item.get("detail_url") or ""
    if not url and bid_id:
        url = f"https://austintexas.bonfirehub.com/opportunities/{bid_id}"
    if not url:
        url = "https://www.austintexas.gov/department/solicitations"

    due_date = None
    for field in ["due_date", "closing_date", "close_date", "response_deadline",
                  "bid_due_date", "dueDate", "closeDate"]:
        raw = item.get(field)
        if raw:
            try:
                raw_str = str(raw)
                if "/" in raw_str:
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
    for field in ["estimated_value", "value", "amount", "budget", "total_value"]:
        raw = item.get(field)
        if raw:
            try:
                value = float(str(raw).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass

    agency = item.get("agency") or item.get("department") or "City of Austin"

    return {
        "source": "austin",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(agency)[:300],
        "naics": item.get("naics") or item.get("naics_code") or "",
        "value": value,
        "due_date": due_date,
        "set_aside": item.get("set_aside") or "",
        "url": url,
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
    print("[austin] Starting scrape...")

    items = fetch_bonfire()
    if not items:
        print("[austin] Bonfire returned nothing, trying Open Data...")
        items = fetch_open_data()
    if not items:
        print("[austin] Open Data returned nothing, trying HTML scrape...")
        items = fetch_solicitations_scrape()

    print(f"[austin] Found {len(items)} raw items")

    contracts = []
    for item in items:
        parsed = parse_item(item)
        if parsed:
            contracts.append(parsed)

    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[austin] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")

    supabase.table("scraper_logs").insert({
        "source": "austin",
        "status": "success" if len(items) >= 0 else "empty",
        "contracts_found": len(items),
        "contracts_new": new_count,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
