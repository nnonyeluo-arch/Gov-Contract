"""
City of Austin Procurement Scraper
Austin uses a self-hosted ColdFusion app at financeonline.austintexas.gov.
Primary: try Excel export endpoint (clean structured data).
Fallback: scrape the HTML solicitations table.
Note: the old austintexas.bonfirehub.com redirect is to Euna Solutions (parent of Bonfire) and is defunct.
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
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://financeonline.austintexas.gov/",
}

BASE_URL = "https://financeonline.austintexas.gov/afo/account_services/solicitation"
EXCEL_URL = f"{BASE_URL}/excel_solicitations.cfm"
HTML_URL  = f"{BASE_URL}/solicitations.cfm"
PORTAL_URL = HTML_URL


def fetch_excel() -> list[dict]:
    """Try the Excel export endpoint — returns structured spreadsheet data."""
    try:
        resp = httpx.get(
            EXCEL_URL,
            params={"ccat": "View All"},
            headers={**HEADERS, "Accept": "application/vnd.ms-excel,*/*"},
            timeout=25,
            follow_redirects=True,
        )
        if resp.status_code == 200 and len(resp.content) > 500:
            import openpyxl
            from io import BytesIO
            wb = openpyxl.load_workbook(BytesIO(resp.content), data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return []
            headers_row = [str(c).strip().lower() if c else "" for c in rows[0]]
            items = []
            for row in rows[1:]:
                item = dict(zip(headers_row, row))
                if any(item.values()):
                    items.append(item)
            print(f"[austin] Excel export returned {len(items)} rows")
            return items
    except Exception as e:
        print(f"[austin] Excel export failed: {e}")
    return []


def fetch_html() -> list[dict]:
    """Scrape the HTML solicitations table."""
    try:
        resp = httpx.get(
            HTML_URL,
            params={"ccat": "View All"},
            headers={**HEADERS, "Accept": "text/html"},
            timeout=25,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            print(f"[austin] HTML fetch status {resp.status_code}")
            return []
        return _parse_html(resp.text)
    except Exception as e:
        print(f"[austin] HTML fetch failed: {e}")
    return []


def _parse_html(html: str) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(html, "html.parser")
        items = []
        # The ColdFusion app renders a standard HTML table
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            link = row.find("a", href=True)
            title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            if not title or title.lower() in ("description", "title", "solicitation"):
                continue
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://financeonline.austintexas.gov{href}"
            all_text = row.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            # Look for a solicitation number like "CLMC123" or "RFP 2024-001"
            bid_id = ""
            for cell in cells:
                text = cell.get_text(strip=True)
                if re.match(r"[A-Z]{2,6}[\s\-]?\d+", text):
                    bid_id = text
                    break
            items.append({
                "id": bid_id or title[:40],
                "title": title,
                "url": href or PORTAL_URL,
                "due_date": dates[-1] if dates else None,
                "source_label": "City of Austin",
            })
        return items
    except Exception as e:
        print(f"[austin] HTML parse error: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    # Excel column names vary; try common patterns
    bid_id = (
        item.get("solicitation number") or item.get("bid number") or item.get("number")
        or item.get("id") or item.get("solicitation_number") or ""
    )
    title = (
        item.get("description") or item.get("title") or item.get("name")
        or item.get("solicitation description") or ""
    )
    if isinstance(title, str):
        title = title.strip()
    if not title:
        return None

    url = item.get("url") or item.get("link") or PORTAL_URL

    due_date = None
    for field in ["closing date", "due date", "deadline", "closingdate", "due_date", "closing_date"]:
        raw = item.get(field)
        if raw:
            try:
                raw_str = str(raw).strip()
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
    for field in ["estimated value", "value", "amount", "budget", "estimated_value"]:
        raw = item.get(field)
        if raw:
            try:
                value = float(str(raw).replace(",", "").replace("$", "").strip())
                break
            except (ValueError, TypeError):
                pass

    agency = item.get("department") or item.get("agency") or item.get("issuing department") or "City of Austin"

    return {
        "source": "austin",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(agency)[:300],
        "naics": str(item.get("naics") or item.get("naics code") or ""),
        "value": value,
        "due_date": due_date,
        "set_aside": str(item.get("set_aside") or ""),
        "url": url,
        "raw_html": str(item.get("scope") or item.get("description") or "")[:5000],
    }


def upsert_contracts(contracts):
    if not contracts:
        return 0
    result = supabase.table("contracts").upsert(contracts, on_conflict="source,source_id", ignore_duplicates=True).execute()
    return len(result.data) if result.data else 0


def run():
    start = time.time()
    print("[austin] Starting scrape...")
    items = fetch_excel()
    if not items:
        print("[austin] Excel export empty, trying HTML scrape...")
        items = fetch_html()
    print(f"[austin] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[austin] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "austin", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": None if items else "No data from Excel or HTML endpoints",
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
