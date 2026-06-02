"""
Portal diagnostic script — run locally to see what each URL actually returns.
Usage: python test_portals.py
No Supabase needed — just reads URLs and reports what comes back.
"""

import httpx
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
}

PORTALS = [
    ("TxSmartBuy",    "https://www.txsmartbuy.gov/sp",                                      "html"),
    ("SAM.gov",       "https://api.sam.gov/opportunities/v2/search?limit=5&pPlace=TX",       "json"),
    ("Austin Excel",  "https://financeonline.austintexas.gov/afo/account_services/solicitation/excel_solicitations.cfm?ccat=View+All", "excel"),
    ("Austin HTML",   "https://financeonline.austintexas.gov/afo/account_services/solicitation/solicitations.cfm?ccat=View+All", "html"),
    ("San Antonio",   "https://webapp1.sanantonio.gov/BidContractOpps/Default.aspx",         "html"),
    ("Fort Worth",    "https://fortworthtexas.bonfirehub.com/portal/?tab=openOpportunities", "html"),
    ("Harris County", "https://bids.hctx.net/bso/external/publicBids.sdo",                  "html"),
    ("Travis County", "https://www.bidnetdirect.com/texas/traviscounty",                     "html"),
    ("TxDOT",         "https://www.txdot.gov/business/road-bridge-maintenance/contract-letting.html", "html"),
    ("Houston Beacon","https://www.beaconbid.com/solicitations/city-of-houston/open",        "html"),
]

def check_html(text, url):
    """Determine if HTML response has real data or is a JS shell."""
    lower = text.lower()
    # Signs of JS-rendered shell (no real data)
    js_shell_signals = [
        'id="root"', 'id="app"', 'window.__initial',
        '<noscript>you need to enable javascript',
        'bundle.js', 'main.js', 'chunk.js',
    ]
    # Signs of real server-rendered data
    data_signals = [
        '<table', '<tr', '<td', 'solicitation', 'bid', 'rfp', 'contract',
        'proposal', 'procurement',
    ]
    js_score = sum(1 for s in js_shell_signals if s in lower)
    data_score = sum(1 for s in data_signals if s in lower)

    # Count table rows as strong signal
    rows = len(re.findall(r'<tr[\s>]', text, re.I))

    if js_score >= 2 and data_score < 3:
        return f"⚠️  JS shell (React/Vue SPA) — Playwright needed"
    elif rows > 5:
        return f"✅ Real HTML table ({rows} <tr> tags found) — scrapable"
    elif data_score >= 3:
        return f"✅ HTML with data signals — likely scrapable"
    elif rows > 0:
        return f"⚡ Partial ({rows} rows) — may need investigation"
    else:
        return f"❓ Unclear — {data_score} data signals, {js_score} JS signals"

print(f"\n{'='*70}")
print("PORTAL DIAGNOSTIC")
print(f"{'='*70}\n")

for name, url, kind in PORTALS:
    print(f"Testing: {name}")
    print(f"  URL: {url}")
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        size = len(resp.content)
        final_url = str(resp.url)
        redirected = " (redirected)" if final_url != url else ""

        if resp.status_code != 200:
            print(f"  ❌ Status: {resp.status_code}{redirected}")
        elif kind == "json":
            try:
                data = resp.json()
                count = len(data.get("opportunitiesData", data.get("data", data.get("results", []))))
                print(f"  ✅ JSON — {count} items, {size:,} bytes{redirected}")
            except Exception:
                print(f"  ⚠️  Status 200 but not valid JSON — {size:,} bytes{redirected}")
        elif kind == "excel":
            if size > 1000 and (resp.headers.get("content-type","").startswith("application/") or b"PK" in resp.content[:4]):
                print(f"  ✅ Excel file — {size:,} bytes{redirected}")
            else:
                print(f"  ⚠️  Status 200 but doesn't look like Excel — {size:,} bytes, content-type: {resp.headers.get('content-type','unknown')}{redirected}")
        else:
            verdict = check_html(resp.text, url)
            print(f"  {verdict} | {size:,} bytes{redirected}")

    except httpx.TimeoutException:
        print(f"  ❌ TIMEOUT (>15s)")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
    print()

print("="*70)
print("LEGEND:")
print("  ✅ = Should work with plain HTTP scraping")
print("  ⚠️  = JS-rendered — needs Playwright")
print("  ❌ = Connection failed / bad URL")
print("="*70)
