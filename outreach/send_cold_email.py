"""
TX Contract Intel — Cold Email Sender
Usage: python send_cold_email.py

Pulls 2-3 live matching contracts from Supabase, previews the email,
then sends it via Resend with open/click tracking.

Requires env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  RESEND_API_KEY
"""

import os
import random
import httpx
from datetime import date, timedelta
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── trade → category/keyword mapping ─────────────────────────────────────────

TRADE_MAP = {
    "it":           {"categories": ["IT"], "keywords": ["technology", "software", "IT", "network", "cyber", "data", "system"]},
    "staffing":     {"categories": ["IT", "staffing"], "keywords": ["staffing", "augmentation", "staff", "personnel", "workforce"]},
    "construction": {"categories": ["construction"], "keywords": ["construction", "building", "renovation", "facility", "infrastructure", "road", "bridge"]},
    "janitorial":   {"categories": ["maintenance"], "keywords": ["janitorial", "cleaning", "custodial", "sanitation"]},
    "healthcare":   {"categories": ["healthcare"], "keywords": ["health", "medical", "clinical", "nursing", "hospital"]},
    "professional": {"categories": ["professional_services"], "keywords": ["consulting", "advisory", "management", "professional services"]},
    "roofing":      {"categories": ["construction"], "keywords": ["roof", "roofing", "waterproof", "building envelope"]},
    "security":     {"categories": ["IT", "professional_services"], "keywords": ["security", "guard", "surveillance", "access control"]},
}

SUBJECT_VARIANTS = [
    "3 Texas {trade} bids closing this month",
    "{agency} is buying {trade} services",
    "Bids {company} might be missing",
]


# ── contract fetching ─────────────────────────────────────────────────────────

def fetch_matching_contracts(trade: str, limit: int = 3) -> list[dict]:
    """Pull live contracts matching trade. Skips anything closing within 7 days."""
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=7)).isoformat()
    trade_lower = trade.lower().strip()

    # Find closest trade key
    trade_key = None
    for key in TRADE_MAP:
        if key in trade_lower or trade_lower in key:
            trade_key = key
            break

    categories = TRADE_MAP.get(trade_key, {}).get("categories", []) if trade_key else []
    keywords = TRADE_MAP.get(trade_key, {}).get("keywords", [trade_lower]) if trade_key else [trade_lower]

    try:
        result = supabase.table("enriched_contracts")\
            .select("*, contracts(*)")\
            .order("complexity_score", desc=False)\
            .limit(300)\
            .execute()

        rows = result.data or []
        matched = []

        for row in rows:
            contract = row.get("contracts") or {}
            if isinstance(contract, list):
                contract = contract[0] if contract else {}

            due = contract.get("due_date") or ""
            # Skip expired or closing too soon
            if due and due < cutoff:
                continue

            cat = (row.get("category") or "").lower()
            title = (contract.get("title") or "").lower()
            summary = (row.get("summary") or "").lower()

            cat_match = any(c.lower() in cat for c in categories)
            kw_match = any(kw.lower() in title or kw.lower() in summary for kw in keywords)

            if cat_match or kw_match:
                matched.append({
                    "title": contract.get("title", "Contract Opportunity"),
                    "agency": contract.get("agency", "Texas Agency"),
                    "value": contract.get("value"),
                    "due_date": due,
                    "url": contract.get("url", "https://txcontractintel.com"),
                    "summary": row.get("summary", ""),
                })

            if len(matched) >= limit:
                break

        return matched[:limit]

    except Exception as e:
        print(f"Error fetching contracts: {e}")
        return []


# ── email construction ────────────────────────────────────────────────────────

def build_email(first_name: str, company: str, trade: str, contracts: list[dict]) -> tuple[str, str]:
    trade_label = trade.title()

    contract_lines = []
    for i, c in enumerate(contracts, 1):
        close = c["due_date"] if c.get("due_date") else "see listing"
        contract_lines.append(
            f"{i}. {c['title']} — {c['agency']}, closes {close}"
        )
    contracts_text = "\n".join(contract_lines)

    # Rotate subject lines — track which variant gets replies
    variant = random.choice(SUBJECT_VARIANTS)
    subject = variant.format(
        trade=trade_label,
        agency=contracts[0]["agency"] if contracts else "Texas Agency",
        company=company,
    )

    body = f"""{first_name},

Found these while tracking Texas government contract postings. All open now and match {company}'s work:

{contracts_text}

We monitor 9 Texas sources daily (SAM.gov, TxSmartBuy, TxDOT, and the major city and county portals) so contractors stop missing bids buried across different sites.

Every Monday subscribers get a digest like this matched to their trade. First 30 days are free if you want to try it.

Worth a look?

Okafor
TX Contract Intel
txcontractintel.com"""

    return subject, body


# ── sending ───────────────────────────────────────────────────────────────────

def send_via_resend(to_email: str, subject: str, body: str, company: str) -> bool:
    safe_company = company[:50].replace(" ", "_").lower()

    payload = {
        "from": "Okafor · TX Contract Intel <okafor@txcontractintel.com>",
        "to": [to_email],
        "subject": subject,
        "text": body,
        "tags": [
            {"name": "campaign", "value": "cold_outreach"},
            {"name": "company", "value": safe_company},
        ],
    }

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        print(f"  ✅ Sent — Resend ID: {result.get('id')}")
        return True
    except httpx.HTTPStatusError as e:
        print(f"  ❌ Resend HTTP error {e.response.status_code}: {e.response.text}")
        return False
    except Exception as e:
        print(f"  ❌ Send failed: {e}")
        return False


def log_sent(company: str, to_email: str, trade: str) -> None:
    """Append to prospects.csv so follow-up script can track who needs day-4."""
    import csv, pathlib
    log_path = pathlib.Path(__file__).parent / "prospects.csv"
    write_header = not log_path.exists()

    with open(log_path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["company", "email", "trade", "sent_date", "status", "notes"])
        writer.writerow([company, to_email, trade, date.today().isoformat(), "sent", ""])

    print(f"  📋 Logged to prospects.csv")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== TX Contract Intel — Cold Email Sender ===\n")
    company  = input("Company name: ").strip()
    contact  = input("Contact name (first last): ").strip()
    to_email = input("Email address: ").strip()
    trade    = input("Trade (IT / staffing / construction / janitorial / healthcare / professional / roofing / security): ").strip()

    print(f"\nSearching for live {trade} contracts...")
    contracts = fetch_matching_contracts(trade)

    if len(contracts) < 2:
        print(f"\n⚠️  Only {len(contracts)} match(es) found — skipping. Need at least 2 for a credible email.")
        print("Try a broader trade keyword, or check that open contracts exist in the DB.")
        return

    first_name = contact.split()[0] if contact else "there"
    subject, body = build_email(first_name, company, trade, contracts)

    print("\n" + "=" * 60)
    print("PREVIEW")
    print("=" * 60)
    print(f"To:      {to_email}")
    print(f"Subject: {subject}")
    print()
    print(body)
    print("=" * 60)

    confirm = input("\nSend this? (yes / no): ").strip().lower()
    if confirm != "yes":
        print("Cancelled.")
        return

    sent = send_via_resend(to_email, subject, body, company)
    if sent:
        log_sent(company, to_email, trade)


if __name__ == "__main__":
    main()
