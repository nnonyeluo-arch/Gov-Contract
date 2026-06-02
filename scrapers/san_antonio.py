"""
City of San Antonio Procurement Scraper
San Antonio uses IonWave / DemandStar for solicitations,
accessible via their purchasing office portal.
Falls back to Bexar County open data.
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

# San Antonio uses IonWave for purchasing
IONWAVE_BASE = "https://sanantonio.ionwave.net"
IONWAVE_API  = "https://sanantonio.ionwave.net/api/solicitations"
# COSA direct purchasing page (HTML fallback)
COSA_URL = "https://www.sanantonio.gov/Finance/Purchasing/Open-Solicitations"
# DemandStar (secondary platform some SA entities use)
DEMANDSTAR_API = "https://network.demandstar.com/api/bids"


def fetch_demandstar() -> list[dict]:
    """Fetch San Antonio bids from DemandStar API."""
    all_items = []
    for page in range(1, 6):
        try:
            resp = httpx.get(
                DEMANDSTAR_API,
                params={
                    "agency": "city-of-san-antonio",
                    "status": "open",
                    "page": page,
                    "per_page": 100,
                },
                headers=HEADERS,
                timeout=20,
                follow_redirects=True,
            )
            if resp.status_code in (403, 404):
                print(f"[san_antonio] DemandStar returned {resp.status_code}")
                break
            resp.raise_for_status()
            data = resp.json()
            items = data.get("bids") or data.get("data") or data.get("results") or []
            if not items:
                break
            all_items.extend(items)
            if len(items) < 100:
                break
            time.sleep(1)
        except Exception as e:
            print(f"[san_antonio] DemandStar error page {page}: {e}")
            break
    return all_items


def fetch_cosa_scrape() -> list[dict]:
    """Scrape COSA open solicitations page."""
    try:
        resp = httpx.get(
            COSA_URL,
            headers={**HEADERS, "Accept": "text/html"},
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        import re

        soup = BeautifulSoup(resp.text, "html.parser")
        items = []

        for row in soup.select("table tr, .solicitation-row, .bid-row"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link_tag = row.find("a", href=True)
            title = link_tag.get_text(strip=True) if link_tag else cells[0].get_text(strip=True)
            if not title or title.lower() in ("title", "solicitation", "description"):
                continue
            href = link_tag["href"] if link_tag else ""
            if href and not href.startswith("http"):
                href = f"https://www.sanantonio.gov{href}"

            all_text = row.get_text(" ")
            date_matches = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            bid_id = ""
            for cell in cells:
                text = cell.get_text(strip=True)
                if re.match(r"[A-Z0-9]{2,}-\d+", text) or (text.startswith("IFB") or text.startswith("RFP") or text.startswith("RFQ")):
                    bid_id = text
                    break

            items.append({
                "id": bid_id or title[:40],
                "title": title,
                "url": href or COSA_URL,
                "due_date": date_matches[-1] if date_matches else None,
            })
        return items
    except Exception as e:
        print(f"[san_antonio] HTML scrape failed: {e}")
        return []


def fetch_ionwave() -> list[dict]:
    """Try IonWave API — San Antonio's primary procurement platform."""
    for api_url in [IONWAVE_API, f"{IONWAVE_BASE}/CurrentSolicitations.aspx"]:
        try:
            resp = httpx.get(
                api_url,
                params={"status": "open", "format": "json"},
                headers=HEADERS,
                timeout=20,
                follow_redirects=True,
            )
            if resp.status_code in (400, 403, 404, 405):
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data.get("solicitations") or data.get("data") or data.get("results") or []
                    if items:
                        print(f"[san_antonio] IonWave returned {len(items)} items")
                        return items
                except Exception:
                    # HTML response — parse it
                    return _parse_ionwave_html(resp.text)
        except Exception as e:
            print(f"[san_antonio] IonWave {api_url} error: {e}")
    return []


def _parse_ionwave_html(html: str) -> list[dict]:
    """Parse IonWave solicitations HTML page."""
    try:
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(html, "html.parser")
        items = []
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link_tag = row.find("a", href=True)
            title = link_tag.get_text(strip=True) if link_tag else cells[0].get_text(strip=True)
            if not title or title.lower() in ("title", "description", "solicitation"):
                continue
            href = link_tag["href"] if link_tag else ""
            if href and not href.startswith("http"):
                href = f"{IONWAVE_BASE}{href}"
            all_text = row.get_text(" ")
            date_matches = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            bid_id = ""
            for cell in cells:
                text = cell.get_text(strip=True)
                if re.match(r"[A-Z0-9]{2,}-\d+", text):
                    bid_id = text
                    break
            items.append({
                "id": bid_id or title[:40],
                "title": title,
                "url": href or COSA_URL,
                "due_date": date_matches[-1] if date_matches else None,
            })
        return items
    except Exception as e:
        print(f"[san_antonio] HTML parse error: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    """Normalize a record into our contracts schema."""
    bid_id = (
        item.get("id") or item.get("bid_id") or item.get("solicitation_number")
        or item.get("bidNumber") or ""
    )
    title = (item.get("title") or item.get("name") or item.get("description") or "").strip()
    if not title:
        return None

    url = item.get("url") or item.get("link") or item.get("detail_url") or ""
    if not url and bid_id:
        url = f"https://network.demandstar.com/bids/{bid_id}"
    if not url:
        url = COSA_URL

    due_date = None
    for field in ["due_date", "closing_date", "close_date", "response_deadline",
                  "bid_due_date", "dueDate", "closeDate", "openingDate"]:
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
    for field in ["estimated_value", "value", "amount", "budget"]:
        raw = item.get(field)
        if raw:
            try:
                value = float(str(raw).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass

    agency = item.get("agency") or item.get("department") or "City of San Antonio"

    return {
        "source": "san_antonio",
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
    print("[san_antonio] Starting scrape...")

    items = fetch_ionwave()
    if not items:
        print("[san_antonio] IonWave returned nothing, trying DemandStar...")
        items = fetch_demandstar()
    if not items:
        print("[san_antonio] Trying HTML scrape...")
        items = fetch_cosa_scrape()

    print(f"[san_antonio] Found {len(items)} raw items")

    contracts = []
    for item in items:
        parsed = parse_item(item)
        if parsed:
            contracts.append(parsed)

    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[san_antonio] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")

    supabase.table("scraper_logs").insert({
        "source": "san_antonio",
        "status": "success" if len(items) >= 0 else "empty",
        "contracts_found": len(items),
        "contracts_new": new_count,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
