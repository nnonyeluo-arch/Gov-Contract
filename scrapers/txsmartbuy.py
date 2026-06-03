"""
TxSmartBuy / Texas ESBD Scraper
The Electronic State Business Daily (ESBD) at txsmartbuy.gov/esbd lists all TX state agency bids.
The page loads fine (~88KB) but uses a non-standard layout — we try every selector pattern.
"""

import os
import re
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://www.txsmartbuy.gov/",
}

ESBD_URLS = [
    "https://www.txsmartbuy.gov/esbd",
    "https://www.txsmartbuy.gov/esbd?category=Open&type=Bid",
    "https://www.txsmartbuy.gov/sp",
]


def fetch_esbd() -> list[dict]:
    for url in ESBD_URLS:
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if resp.status_code != 200:
                print(f"[txsmartbuy] {url} → {resp.status_code}")
                continue
            items = _parse_esbd_html(resp.text, str(resp.url))
            if items:
                print(f"[txsmartbuy] Got {len(items)} items from {str(resp.url)[:70]}")
                return items
            else:
                print(f"[txsmartbuy] {str(resp.url)[:70]} returned HTML but 0 parseable bids ({len(resp.text):,} bytes)")
                # Debug: show what links exist on the page
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = [a.get("href","") for a in soup.find_all("a", href=True) if "esbd" in a.get("href","").lower() or "bid" in a.get("href","").lower()]
                    print(f"[txsmartbuy] Bid-related links found: {links[:5]}")
                    # Show what the page title/h1 says
                    h1 = soup.find("h1")
                    title = soup.find("title")
                    print(f"[txsmartbuy] Page title: {title.get_text() if title else 'none'} | H1: {h1.get_text() if h1 else 'none'}")
                except Exception:
                    pass
        except Exception as e:
            print(f"[txsmartbuy] {url} error: {e}")
    return []


def _parse_esbd_html(html: str, base_url: str) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        domain = "/".join(base_url.split("/")[:3])
        items = []

        # Strategy 1: standard table rows
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link = row.find("a", href=True)
            title = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            if not title or len(title) < 5 or title.lower() in ("title", "description", "bid title", "solicitation"):
                continue
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"{domain}{href}"
            all_text = row.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            bid_id = ""
            for cell in cells:
                t = cell.get_text(strip=True)
                if re.match(r"\d{4}-\d+|[A-Z]{2,4}-\d+", t):
                    bid_id = t
                    break
            items.append({"id": bid_id or title[:40], "title": title,
                          "url": href or base_url, "due_date": dates[-1] if dates else None,
                          "agency": cells[1].get_text(strip=True) if len(cells) > 1 else ""})

        if items:
            return items

        # Strategy 2: list items / divs with bid-like content
        for el in soup.select("li, .bid, .solicitation, .opportunity, [class*='bid'], [class*='result'], .item"):
            link = el.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            href = link["href"]
            if not href.startswith("http"):
                href = f"{domain}{href}"
            all_text = el.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            items.append({"id": title[:40], "title": title, "url": href, "due_date": dates[-1] if dates else None, "agency": ""})

        if items:
            return items

        # Strategy 3: any link pointing to a bid detail page
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)
            # ESBD detail pages typically contain "bidId=" or "/esbd/" in the URL
            if len(text) > 10 and ("bidId=" in href or "/esbd/" in href or "bid_id=" in href or "solicitation" in href.lower()):
                if not href.startswith("http"):
                    href = f"{domain}{href}"
                items.append({"id": text[:40], "title": text, "url": href, "due_date": None, "agency": ""})

        return items
    except Exception as e:
        print(f"[txsmartbuy] HTML parse error: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    bid_id = item.get("id") or ""
    title = (item.get("title") or item.get("description") or "").strip()
    if not title or len(title) < 5:
        return None
    url = item.get("url") or "https://www.txsmartbuy.gov/esbd"
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
    return {
        "source": "txsmartbuy",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(item.get("agency") or "Texas State Agency")[:300],
        "naics": "",
        "value": None,
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
    print("[txsmartbuy] Starting scrape...")
    items = fetch_esbd()
    print(f"[txsmartbuy] Total raw items: {len(items)}")
    contracts = [p for item in items if (p := parse_item(item))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)
    print(f"[txsmartbuy] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")
    supabase.table("scraper_logs").insert({
        "source": "txsmartbuy", "status": "success" if items else "empty",
        "contracts_found": len(items), "contracts_new": new_count,
        "error_message": None if items else "All ESBD endpoints returned 0 parseable bids",
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
