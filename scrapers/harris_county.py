"""
Harris County Procurement Scraper
Harris County uses their own purchasing portal and IonWave.
Separate from City of Houston — large independent budget.
"""

import os
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

IONWAVE_BASE = "https://harriscountytx.ionwave.net"
PORTAL_URL = "https://www.hctx.net/purchasing/bids-and-rfps"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
}


def fetch_ionwave() -> list[dict]:
    """Try Harris County IonWave API."""
    endpoints = [
        f"{IONWAVE_BASE}/api/solicitations",
        f"{IONWAVE_BASE}/CurrentSolicitations.aspx",
    ]
    for endpoint in endpoints:
        try:
            resp = httpx.get(
                endpoint,
                params={"status": "open", "format": "json"},
                headers=HEADERS,
                timeout=20,
                follow_redirects=True,
            )
            if resp.status_code in (400, 401, 403, 404, 405):
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data.get("solicitations") or data.get("data") or data.get("results") or []
                    if items:
                        return items
                except Exception:
                    items = _parse_ionwave_html(resp.text)
                    if items:
                        return items
        except Exception as e:
            print(f"[harris_county] IonWave {endpoint} error: {e}")
    return []


def _parse_ionwave_html(html: str) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(html, "html.parser")
        items = []
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link = row.find("a", href=True)
            title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            if not title or title.lower() in ("title", "description"):
                continue
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"{IONWAVE_BASE}{href}"
            all_text = row.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            bid_id = ""
            for cell in cells:
                text = cell.get_text(strip=True)
                if re.match(r"[A-Z0-9]{2,}-\d+|\d{4}-\d+", text):
                    bid_id = text
                    break
            items.append({"id": bid_id or title[:40], "title": title, "url": href or PORTAL_URL, "due_date": dates[-1] if dates else None})
        return items
    except Exception as e:
        print(f"[harris_county] HTML parse error: {e}")
        return []


def fetch_portal_scrape() -> list[dict]:
    """Fallback: scrape Harris County purchasing page."""
    try:
        resp = httpx.get(PORTAL_URL, headers={**HEADERS, "Accept": "text/html"}, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return []
        return _parse_ionwave_html(resp.text)
    except Exception as e:
        print(f"[harris_county] Portal scrape error: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    bid_id = item.get("id") or item.get("bid_id") or item.get("solicitation_number") or item.get("number") or ""
    title = (item.get("title") or item.get("name") or item.get("description") or "").strip()
    if not title:
        return None

    url = item.get("url") or item.get("link") or ""
    if not url:
        url = PORTAL_URL

    due_date = None
    for field in ["due_date", "closing_date", "close_date", "dueDate", "closingdate"]:
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
        "source": "harris_county",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": item.get("department") or "Harris County",
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
    print("[harris_county] Starting scrape...")
    items = fetch_ionwave()
    if not items:
        print("[harris_county] IonWave returned nothing, trying portal scrape...")
        items = fetch_portal_scrape()
    print(f"[harris_county] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[harris_county] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "harris_county", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": "All endpoints returned 0" if not items else None,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
