"""
TxDOT Procurement Scraper
TxDOT contract lettings page confirmed loading (190KB) but uses div-based layout, not tables.
We scrape letting dates/projects and also try the Plans Online bid lettings page.
"""

import os
import re
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

LETTING_PAGE   = "https://www.txdot.gov/business/road-bridge-maintenance/contract-letting.html"
PLANS_PAGE     = "https://www.txdot.gov/business/plans-online-bid-lettings.html"
OPEN_DATA_URL  = "https://data.txdot.gov/resource/chhm-xrjn.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
}


def fetch_letting_page() -> list[dict]:
    for url in [LETTING_PAGE, PLANS_PAGE]:
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if resp.status_code != 200:
                continue
            items = _parse_txdot_html(resp.text, url)
            if items:
                print(f"[txdot] Got {len(items)} items from {url}")
                return items
            print(f"[txdot] {url} loaded ({len(resp.text):,} bytes) but 0 bids parsed")
        except Exception as e:
            print(f"[txdot] {url} error: {e}")
    return []


def _parse_txdot_html(html: str, base_url: str) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        items = []

        # TxDOT uses divs AND tables — try both
        # Pattern 1: standard table rows
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link = row.find("a", href=True)
            title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            if not title or len(title) < 5 or title.lower() in ("description", "project", "letting"):
                continue
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://www.txdot.gov{href}"
            all_text = row.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            amounts = re.findall(r"\$[\d,]+", all_text)
            value = None
            if amounts:
                try:
                    value = float(amounts[0].replace("$", "").replace(",", ""))
                except Exception:
                    pass
            items.append({"id": title[:40], "title": title, "url": href or LETTING_PAGE,
                          "due_date": dates[0] if dates else None, "value": value, "department": "TxDOT"})

        if items:
            return items

        # Pattern 2: div-based letting schedule (TxDOT's actual layout)
        # Look for divs/sections that contain letting dates and project descriptions
        for section in soup.select(".letting-item, .project-item, [class*='letting'], [class*='project'], article, .card"):
            link = section.find("a", href=True)
            title_el = section.find(["h2", "h3", "h4", "strong", "b"])
            title = (title_el or link or section).get_text(strip=True)[:200] if (title_el or link) else ""
            if not title or len(title) < 5:
                continue
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://www.txdot.gov{href}"
            all_text = section.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            amounts = re.findall(r"\$[\d,]+", all_text)
            value = None
            if amounts:
                try:
                    value = float(amounts[0].replace("$","").replace(",",""))
                except Exception:
                    pass
            items.append({"id": title[:40], "title": title, "url": href or LETTING_PAGE,
                          "due_date": dates[0] if dates else None, "value": value, "department": "TxDOT"})

        if items:
            return items

        # Pattern 3: any link that looks like a letting/project
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)
            if len(text) > 10 and any(kw in text.lower() for kw in ["letting", "project", "highway", "bridge", "fm ", "sh ", "us "]):
                if not href.startswith("http"):
                    href = f"https://www.txdot.gov{href}"
                items.append({"id": text[:40], "title": text, "url": href,
                              "due_date": None, "value": None, "department": "TxDOT"})

        return items[:50]  # cap at 50 from link scan
    except Exception as e:
        print(f"[txdot] HTML parse error: {e}")
        return []


def fetch_open_data() -> list[dict]:
    try:
        resp = httpx.get(OPEN_DATA_URL,
            params={"$limit": 200, "$order": "letting_date DESC"},
            headers={**HEADERS, "Accept": "application/json"}, timeout=20, follow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                print(f"[txdot] Open Data returned {len(data)} records")
                return data
    except Exception as e:
        print(f"[txdot] Open Data error: {e}")
    return []


def parse_item(item: dict) -> dict | None:
    bid_id = item.get("control_section") or item.get("id") or item.get("project_id") or item.get("contract_id") or ""
    title = (item.get("description") or item.get("title") or item.get("project_description") or item.get("work_type") or "").strip()
    if not title:
        return None
    url = item.get("url") or item.get("link") or LETTING_PAGE
    due_date = None
    for field in ["letting_date", "due_date", "bid_date", "closing_date"]:
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
    value = item.get("value") or item.get("estimated_cost") or item.get("low_bid") or item.get("amount")
    if value:
        try:
            value = float(str(value).replace(",","").replace("$",""))
        except (ValueError, TypeError):
            value = None
    county = item.get("county") or item.get("location") or ""
    return {
        "source": "txdot",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": f"TxDOT{' — ' + county if county else ''}"[:300],
        "naics": "237310",
        "value": value,
        "due_date": due_date,
        "set_aside": "",
        "url": url,
        "raw_html": "",
    }


def upsert_contracts(contracts):
    if not contracts:
        return 0
    result = supabase.table("contracts").upsert(contracts, on_conflict="source,source_id", ignore_duplicates=True).execute()
    return len(result.data) if result.data else 0


def run():
    start = time.time()
    print("[txdot] Starting scrape...")
    items = fetch_letting_page()
    if not items:
        print("[txdot] HTML returned 0, trying Open Data...")
        items = fetch_open_data()
    print(f"[txdot] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[txdot] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "txdot", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": None if items else "All TxDOT endpoints returned 0",
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
