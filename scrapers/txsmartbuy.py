"""
TxSmartBuy Scraper
Texas Health and Human Services / DIR / TxSmartBuy cooperative purchasing portal.
Scrapes open solicitations from the public search API.
"""

import os
import time
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# TxSmartBuy open solicitations search
TXSB_BASE = "https://www.txsmartbuy.gov"
TXSB_API  = f"{TXSB_BASE}/esbd"  # Electronic State Business Daily
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TXContractIntelBot/1.0)",
    "Accept": "application/json, text/html",
}


def fetch_esbd_page(page: int = 1, per_page: int = 100) -> list[dict]:
    """
    Pull open bids from the TX ESBD JSON endpoint.
    Falls back to the legacy ESBD XML/HTML if JSON fails.
    """
    # Try JSON API first
    try:
        resp = httpx.get(
            f"{TXSB_BASE}/api/bids",
            params={"status": "open", "page": page, "per_page": per_page},
            headers=HEADERS,
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("bids") or data.get("data") or data.get("results") or []
            if items:
                return items
    except Exception:
        pass

    # Fallback: ESBD open bids search (HTML scrape)
    try:
        resp = httpx.get(
            "https://www.txsmartbuy.gov/esbd",
            params={"status": "open", "pageNo": page},
            headers=HEADERS,
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        items = []
        # ESBD table rows
        rows = soup.select("table.esbd-results tr") or soup.select("tr.bid-row")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # Try to extract a link and title
            link_tag = row.find("a", href=True)
            title = link_tag.get_text(strip=True) if link_tag else cells[0].get_text(strip=True)
            href = link_tag["href"] if link_tag else ""
            if href and not href.startswith("http"):
                href = f"{TXSB_BASE}{href}"

            # Extract bid number (often first cell or in link)
            bid_id = ""
            for cell in cells:
                text = cell.get_text(strip=True)
                if text and (text.startswith("2") or "-" in text) and len(text) < 30:
                    bid_id = text
                    break

            # Extract dates — look for MM/DD/YYYY patterns
            import re
            all_text = row.get_text(" ")
            date_matches = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)

            items.append({
                "id": bid_id or title[:40],
                "title": title,
                "url": href,
                "agency": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                "bid_opening_date": date_matches[-1] if date_matches else None,
                "closing_date": date_matches[-1] if date_matches else None,
            })

        return items

    except Exception as e:
        print(f"[txsmartbuy] HTML scrape error page {page}: {e}")
        return []


def parse_item(item: dict) -> dict | None:
    """Normalize a TxSmartBuy bid record into our contracts schema."""
    # Accept multiple field name patterns from different API versions
    bid_id = (
        item.get("bid_id") or item.get("id") or item.get("solicitation_number")
        or item.get("bidNumber") or item.get("number") or ""
    )
    title = (
        item.get("title") or item.get("bid_title") or item.get("description") or ""
    ).strip()

    if not title:
        return None

    # Source URL
    url = item.get("url") or item.get("link") or item.get("detail_url") or ""
    if not url and bid_id:
        url = f"https://www.txsmartbuy.gov/esbd/{bid_id}"
    if not url:
        url = "https://www.txsmartbuy.gov/esbd"

    # Due date — check multiple field names
    due_date = None
    for field in ["closing_date", "bid_closing_date", "due_date", "response_deadline",
                  "bid_opening_date", "openDate", "closeDate", "dueDate"]:
        raw = item.get(field)
        if raw:
            try:
                raw_str = str(raw)
                # Handle MM/DD/YYYY
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

    # Value
    value = None
    for field in ["estimated_value", "value", "amount", "total_value", "budgetAmount"]:
        raw = item.get(field)
        if raw:
            try:
                value = float(str(raw).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass

    agency = (
        item.get("agency") or item.get("agency_name") or item.get("department")
        or item.get("buyerAgency") or "Texas State Agency"
    )

    return {
        "source": "txsmartbuy",
        "source_id": str(bid_id or title[:60]),
        "title": str(title)[:500],
        "agency": str(agency)[:300],
        "naics": item.get("naics") or item.get("naics_code") or "",
        "value": value,
        "due_date": due_date,
        "set_aside": item.get("set_aside") or item.get("hb_class") or "",
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
    print("[txsmartbuy] Starting scrape...")

    all_items = []
    for page in range(1, 6):  # up to 5 pages / 500 records
        items = fetch_esbd_page(page=page)
        if not items:
            break
        all_items.extend(items)
        print(f"[txsmartbuy] Page {page}: {len(items)} items")
        if len(items) < 100:
            break
        time.sleep(1)

    print(f"[txsmartbuy] Total raw items: {len(all_items)}")

    contracts = []
    for item in all_items:
        parsed = parse_item(item)
        if parsed:
            contracts.append(parsed)

    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)

    print(f"[txsmartbuy] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")

    supabase.table("scraper_logs").insert({
        "source": "txsmartbuy",
        "status": "success" if len(all_items) > 0 else "empty",
        "contracts_found": len(all_items),
        "contracts_new": new_count,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
