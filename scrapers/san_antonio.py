"""
City of San Antonio Procurement Scraper
San Antonio uses a server-side ASP.NET app at webapp1.sanantonio.gov/BidContractOpps/.
The page renders a full HTML table with no JS required — fully scrapable with BeautifulSoup.
Pagination uses ASP.NET __doPostBack; we handle pages 1-2 (typically 20 bids per page).
"""

import os
import time
import re
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PORTAL_URL = "https://webapp1.sanantonio.gov/BidContractOpps/Default.aspx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": PORTAL_URL,
}


def _extract_viewstate(html: str) -> dict:
    """Extract ASP.NET hidden fields needed for postback pagination."""
    fields = {}
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for name in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]:
            tag = soup.find("input", {"name": name})
            if tag:
                fields[name] = tag.get("value", "")
    except Exception:
        pass
    return fields


def _parse_table(html: str) -> list[dict]:
    """Parse the bids table from the ASP.NET page HTML."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        items = []
        # The grid is typically id containing "gv" (GridView)
        table = soup.find("table", id=re.compile(r"gv", re.I))
        if not table:
            table = soup.find("table", class_=re.compile(r"grid|bid", re.I))
        if not table:
            for t in soup.find_all("table"):
                if len(t.find_all("tr")) > 3:
                    table = t
                    break
        if not table:
            return []

        rows = table.find_all("tr")
        for row in rows[1:]:  # skip header
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link = row.find("a", href=True)
            title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            if not title or len(title) < 4:
                continue
            href = ""
            if link:
                href = link["href"]
                if not href.startswith("http"):
                    href = f"https://webapp1.sanantonio.gov/BidContractOpps/{href.lstrip('/')}"

            all_text = row.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)

            bid_id = ""
            dept = ""
            for cell in cells:
                text = cell.get_text(strip=True)
                if re.match(r"[A-Z0-9]{2,}-\d+|\d{4}-[A-Z0-9]+-\d+", text) and not bid_id:
                    bid_id = text
                elif len(text) > 3 and len(text) < 60 and not re.search(r"\d{1,2}/\d{1,2}/\d{4}", text) and text != title and not dept:
                    dept = text

            items.append({
                "id": bid_id or title[:40],
                "title": title,
                "url": href or PORTAL_URL,
                "due_date": dates[-1] if dates else None,
                "department": dept or "City of San Antonio",
            })
        return items
    except Exception as e:
        print(f"[san_antonio] HTML parse error: {e}")
        return []


def fetch_page1() -> tuple[list[dict], str]:
    """Fetch the first page of bids. Returns (items, raw_html)."""
    try:
        resp = httpx.get(PORTAL_URL, headers=HEADERS, timeout=25, follow_redirects=True)
        if resp.status_code == 200:
            items = _parse_table(resp.text)
            print(f"[san_antonio] Page 1 returned {len(items)} items")
            return items, resp.text
    except Exception as e:
        print(f"[san_antonio] Fetch page 1 error: {e}")
    return [], ""


def fetch_page2(html: str) -> list[dict]:
    """Post back for page 2 using ASP.NET viewstate."""
    vs = _extract_viewstate(html)
    if not vs.get("__VIEWSTATE"):
        return []
    try:
        data = {
            **vs,
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$gvBidContractOpps",
            "__EVENTARGUMENT": "Page$2",
        }
        resp = httpx.post(
            PORTAL_URL,
            data=data,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=25,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            items = _parse_table(resp.text)
            print(f"[san_antonio] Page 2 returned {len(items)} items")
            return items
    except Exception as e:
        print(f"[san_antonio] Page 2 error: {e}")
    return []


def parse_item(item: dict) -> dict | None:
    bid_id = item.get("id") or item.get("bid_id") or ""
    title = (item.get("title") or item.get("name") or item.get("description") or "").strip()
    if not title:
        return None

    url = item.get("url") or PORTAL_URL

    due_date = None
    raw = item.get("due_date")
    if raw:
        try:
            raw_str = str(raw).strip()
            if "/" in raw_str:
                parts = raw_str.split("/")
                if len(parts) == 3:
                    m, d, y = parts
                    due_date = f"{y.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
            elif "T" in raw_str:
                due_date = raw_str[:10]
            else:
                due_date = raw_str[:10]
        except Exception:
            pass

    value = None
    for field in ["estimated_value", "value", "amount", "budget"]:
        raw_v = item.get(field)
        if raw_v:
            try:
                value = float(str(raw_v).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass

    return {
        "source": "san_antonio",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(item.get("department") or "City of San Antonio")[:300],
        "naics": str(item.get("naics") or ""),
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
    print("[san_antonio] Starting scrape...")
    items, page1_html = fetch_page1()
    if page1_html and len(items) >= 20:
        items += fetch_page2(page1_html)
    print(f"[san_antonio] Found {len(items)} raw items")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[san_antonio] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "san_antonio", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": None if items else "Portal returned 0 items",
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
