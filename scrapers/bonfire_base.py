"""
Generic Bonfire/Euna Procurement Portal Scraper
Used by: fort_worth, dallas, plano, arlington, tarrant_county, bexar_county

All Bonfire portals share the same SPA structure at:
  https://{subdomain}.bonfirehub.com/portal/?tab=openOpportunities

Strategy:
  1. Playwright network intercept — catch XHR to Bonfire's internal API
  2. Playwright HTML scrape — render page, parse opportunity cards
"""

import os
import re
import time
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Titles that are clearly UI button text, not contract names
ARTIFACT_TITLES = {
    "view opportunity", "view details", "learn more", "open opportunity",
    "apply", "bid now", "submit", "details", "more info", "click here",
    "register", "login", "sign in", "your wishlist",
}


# ── Playwright helpers ────────────────────────────────────────────────────────

def _bonfire_network_intercept(portal_url: str) -> list[dict]:
    """Try to catch Bonfire's internal API XHR calls."""
    try:
        from playwright_helper import get_network_json
        # Bonfire's API calls typically contain these patterns
        for pattern in ["solicitation", "opportunit", "bonfireapi", "portal/api", "public/projects", "api"]:
            items = get_network_json(portal_url, api_pattern=pattern, wait_seconds=10)
            if items:
                print(f"  [bonfire] Network intercept: {len(items)} items (pattern: '{pattern}')")
                return items
    except ImportError:
        pass
    except Exception as e:
        print(f"  [bonfire] Network intercept error: {e}")
    return []


def _bonfire_html_scrape(portal_url: str, agency_name: str) -> list[dict]:
    """Playwright HTML scrape with improved title extraction."""
    try:
        from playwright_helper import get_page_html
        html = get_page_html(
            portal_url,
            wait_selector="[class*='opportunity'], [class*='Opportunity'], .MuiCard-root, table tr",
            wait_seconds=8,
        )
        if html:
            return _parse_bonfire_html(html, portal_url, agency_name)
    except ImportError:
        pass
    except Exception as e:
        print(f"  [bonfire] HTML scrape error: {e}")
    return []


def _parse_bonfire_html(html: str, portal_url: str, agency_name: str) -> list[dict]:
    """
    Parse Bonfire HTML. Bonfire opportunity cards typically look like:
      <div class="...opportunity...">
        <h2 class="...title...">RFP 2026-001 IT Staffing Services</h2>
        <span>Closing: 07/15/2026</span>
        <a href="/portal/opportunity/123">View Opportunity</a>
      </div>

    Common bug: using link.text ("View Opportunity") as title.
    Fix: always prefer heading/paragraph text over link text.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        items = []

        # Try increasingly broad card selectors
        cards = (
            soup.select("[class*='opportunity-card'], [class*='OpportunityCard']")
            or soup.select("[class*='opportunity'], [class*='Opportunity']")
            or soup.select(".MuiCard-root, .MuiPaper-root")
            or soup.select("table tr")
            or soup.select("li[class]")
        )

        for card in cards:
            # Get the link for URL extraction (NOT title extraction)
            link = card.find("a", href=True)
            href = ""
            if link:
                href = link.get("href", "")
                if href and not href.startswith("http"):
                    base = re.match(r"https?://[^/]+", portal_url)
                    href = (base.group(0) if base else "") + href

            # Find title — prefer heading elements, NEVER use "View Opportunity" link text
            title = ""
            for selector in ["h2", "h3", "h4", "h1", "strong", "[class*='title']", "[class*='Title']", "p"]:
                el = card.find(selector)
                if el:
                    candidate = el.get_text(strip=True)
                    if candidate and candidate.lower().strip() not in ARTIFACT_TITLES and len(candidate) > 8:
                        title = candidate[:300]
                        break

            # Last resort: full card text minus any link text
            if not title:
                card_text = card.get_text(" ", strip=True)
                # Strip common button/nav phrases
                for phrase in ["View Opportunity", "View Details", "Learn More", "Open Opportunity"]:
                    card_text = card_text.replace(phrase, " ")
                title = " ".join(card_text.split())[:300].strip()

            if not title or title.lower().strip() in ARTIFACT_TITLES or len(title) < 8:
                continue

            # Extract dates (MM/DD/YYYY or YYYY-MM-DD)
            all_text = card.get_text(" ")
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", all_text)
            iso_dates = re.findall(r"\d{4}-\d{2}-\d{2}", all_text)

            # Extract bid number
            bid_id_match = re.search(r"[A-Z0-9]{2,10}-\d{2,6}", all_text)

            items.append({
                "id": bid_id_match.group(0) if bid_id_match else title[:40],
                "title": title,
                "agency": agency_name,
                "url": href or portal_url,
                "due_date": dates[-1] if dates else (iso_dates[-1] if iso_dates else None),
            })

        print(f"  [bonfire] HTML parse: {len(items)} cards found")
        return items

    except Exception as e:
        print(f"  [bonfire] HTML parse error: {e}")
        return []


# ── Item parser ───────────────────────────────────────────────────────────────

def parse_item(item: dict, source: str, agency_name: str, portal_url: str) -> dict | None:
    """Normalize a raw Bonfire item into DB schema."""
    # Title — try multiple field names Bonfire API might use
    title = (
        item.get("title") or item.get("name") or item.get("description")
        or item.get("subject") or item.get("projectTitle") or ""
    ).strip()

    if not title or title.lower().strip() in ARTIFACT_TITLES or len(title) < 5:
        return None

    bid_id = (
        item.get("id") or item.get("bid_id") or item.get("number")
        or item.get("solicitation_number") or item.get("projectNumber")
        or str(title[:60])
    )

    url = item.get("url") or item.get("link") or item.get("href") or portal_url
    if url and not url.startswith("http"):
        base = re.match(r"https?://[^/]+", portal_url)
        url = (base.group(0) if base else "") + url

    # Parse due date from multiple formats
    due_date = None
    for field in ["due_date", "closing_date", "close_date", "dueDate", "closeDate",
                  "closingDate", "deadline", "responseDeadline"]:
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
                elif "-" in raw_str and len(raw_str) >= 10:
                    due_date = raw_str[:10]
                if due_date:
                    break
            except Exception:
                pass

    # Parse value
    value = None
    for field in ["estimated_value", "value", "amount", "budget", "estimatedValue"]:
        raw = item.get(field)
        if raw:
            try:
                value = float(str(raw).replace(",", "").replace("$", "").strip())
                break
            except (ValueError, TypeError):
                pass

    agency = item.get("agency") or item.get("department") or item.get("organization") or agency_name

    return {
        "source": source,
        "source_id": str(bid_id),
        "title": str(title)[:500],
        "agency": str(agency)[:300],
        "naics": str(item.get("naics") or ""),
        "value": value,
        "due_date": due_date,
        "set_aside": str(item.get("set_aside") or ""),
        "url": url,
        "raw_html": str(item.get("description") or item.get("summary") or "")[:5000],
    }


# ── DB upsert ─────────────────────────────────────────────────────────────────

def upsert_contracts(contracts: list[dict]) -> int:
    if not contracts:
        return 0
    result = supabase.table("contracts").upsert(
        contracts, on_conflict="source,source_id", ignore_duplicates=True
    ).execute()
    return len(result.data) if result.data else 0


# ── Main run function ─────────────────────────────────────────────────────────

def run_bonfire(source: str, agency_name: str, portal_url: str) -> None:
    """
    Scrape a Bonfire portal. Call this from each city's scraper module.

    Args:
        source:      Short source key, e.g. "dallas" or "tarrant_county"
        agency_name: Display name, e.g. "City of Dallas" or "Tarrant County"
        portal_url:  Full URL, e.g. "https://dallas.bonfirehub.com/portal/?tab=openOpportunities"
    """
    start = time.time()
    print(f"[{source}] Starting Bonfire scrape: {portal_url}")

    # Try API intercept first
    raw_items = _bonfire_network_intercept(portal_url)

    # Fall back to HTML scrape
    if not raw_items:
        print(f"  [{source}] Network intercept found nothing — trying HTML scrape...")
        raw_items = _bonfire_html_scrape(portal_url, agency_name)

    print(f"[{source}] Raw items: {len(raw_items)}")

    contracts = [p for item in raw_items if (p := parse_item(item, source, agency_name, portal_url))]
    new_count = upsert_contracts(contracts)
    duration = int((time.time() - start) * 1000)

    print(f"[{source}] Done. Parsed: {len(contracts)}, New: {new_count}, Time: {duration}ms")

    supabase.table("scraper_logs").insert({
        "source": source,
        "status": "success" if contracts else ("empty" if not raw_items else "partial"),
        "contracts_found": len(raw_items),
        "contracts_new": new_count,
        "error_message": None if raw_items else "Bonfire returned 0 — check Playwright install",
        "duration_ms": duration,
    }).execute()
