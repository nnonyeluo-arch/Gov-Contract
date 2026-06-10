"""
TX Contract Intel — Batch Cold Email Sender
Usage:
  python send_batch.py                    # sends to all not_contacted rows with valid emails
  python send_batch.py --dry-run          # preview only, nothing sent
  python send_batch.py --trade "IT staffing"  # only rows matching this trade

Reads prospects.csv, skips:
  - rows without an email address
  - rows whose status is NOT "not_contacted"
  - rows where fewer than 2 matching contracts are found

Updates each row's status to "sent" and sets sent_date after a successful send.

Requires env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  RESEND_API_KEY
  FROM_EMAIL   (optional, default: okafor@txcontractintel.com)
  FROM_NAME    (optional, default: Okafor · TX Contract Intel)
"""

import os
import csv
import random
import sys
import time
import httpx
import pathlib
import tempfile
import shutil
from datetime import date, timedelta
from supabase import create_client, Client

# ── Config ────────────────────────────────────────────────────────────────────

SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
FROM_EMAIL     = os.environ.get("FROM_EMAIL", "okafor@txcontractintel.com")
FROM_NAME      = os.environ.get("FROM_NAME",  "Okafor · TX Contract Intel")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PROSPECTS_CSV = pathlib.Path(__file__).parent / "prospects.csv"
SEND_DELAY_SECONDS = 2  # pause between sends to avoid rate limits
MIN_CONTRACTS = 2       # skip prospect if fewer matches found

# ── Trade map (same as send_cold_email.py) ───────────────────────────────────

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


# ── Contract fetching ─────────────────────────────────────────────────────────

def fetch_matching_contracts(trade: str, limit: int = 3) -> list[dict]:
    today  = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=7)).isoformat()
    trade_lower = trade.lower().strip()

    trade_key = next((k for k in TRADE_MAP if k in trade_lower or trade_lower in k), None)
    categories = TRADE_MAP.get(trade_key, {}).get("categories", []) if trade_key else []
    keywords   = TRADE_MAP.get(trade_key, {}).get("keywords", [trade_lower]) if trade_key else [trade_lower]

    try:
        result = supabase.table("enriched_contracts")\
            .select("*, contracts(*)")\
            .order("complexity_score", desc=False)\
            .limit(500)\
            .execute()

        matched = []
        for row in result.data or []:
            contract = row.get("contracts") or {}
            if isinstance(contract, list):
                contract = contract[0] if contract else {}

            # Skip federal SAM.gov
            if (contract.get("source") or "").lower() == "sam_gov":
                continue

            due = contract.get("due_date") or ""
            if due and due < cutoff:
                continue

            title_raw = contract.get("title") or ""
            if title_raw.lower().strip() in ("view opportunity", "your wishlist", ""):
                continue

            cat     = (row.get("category") or "").lower()
            title   = title_raw.lower()
            summary = (row.get("summary") or "").lower()

            cat_match = any(c.lower() in cat for c in categories)
            kw_match  = any(kw.lower() in title or kw.lower() in summary for kw in keywords)

            if cat_match or kw_match:
                source = (contract.get("source") or "").replace("_", " ").title()
                agency_raw = contract.get("agency") or ""
                matched.append({
                    "title":    title_raw,
                    "agency":   agency_raw or (source + " Portal") or "Texas Agency",
                    "value":    contract.get("value"),
                    "due_date": due,
                    "url":      contract.get("url", "https://txcontractintel.com"),
                    "summary":  row.get("summary", ""),
                })
            if len(matched) >= limit:
                break

        return matched[:limit]
    except Exception as e:
        print(f"  ⚠️  Contract fetch error: {e}")
        return []


# ── Email construction ────────────────────────────────────────────────────────

def build_email(first_name: str, company: str, trade: str, contracts: list[dict]) -> tuple[str, str]:
    trade_label = trade.title()
    contract_lines = []
    for i, c in enumerate(contracts, 1):
        close = c["due_date"] if c.get("due_date") else "see listing"
        contract_lines.append(f"{i}. {c['title']} — {c['agency']}, closes {close}")
    contracts_text = "\n".join(contract_lines)

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


# ── Sending ───────────────────────────────────────────────────────────────────

def send_via_resend(to_email: str, subject: str, body: str, company: str) -> bool:
    safe_company = company[:50].replace(" ", "_").lower()
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": f"{FROM_NAME} <{FROM_EMAIL}>",
                "to":   [to_email],
                "subject": subject,
                "text": body,
                "tags": [
                    {"name": "campaign", "value": "cold_outreach"},
                    {"name": "company",  "value": safe_company},
                ],
            },
            timeout=15,
        )
        resp.raise_for_status()
        print(f"  ✅  Sent — Resend ID: {resp.json().get('id')}")
        return True
    except httpx.HTTPStatusError as e:
        print(f"  ❌  Resend {e.response.status_code}: {e.response.text}")
        return False
    except Exception as e:
        print(f"  ❌  Send failed: {e}")
        return False


# ── CSV helpers ───────────────────────────────────────────────────────────────

def load_prospects() -> tuple[list[dict], list[str]]:
    if not PROSPECTS_CSV.exists():
        print("prospects.csv not found.")
        return [], []
    with open(PROSPECTS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return rows, fieldnames


def save_prospects(rows: list[dict], fieldnames: list[str]) -> None:
    tmp = pathlib.Path(tempfile.mktemp())
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    shutil.move(str(tmp), str(PROSPECTS_CSV))


def mark_sent(rows: list[dict], email: str) -> None:
    for row in rows:
        if row.get("email", "").strip().lower() == email.strip().lower():
            row["status"]    = "sent"
            row["sent_date"] = date.today().isoformat()
            break


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    dry_run      = "--dry-run" in sys.argv
    trade_filter = None
    if "--trade" in sys.argv:
        idx = sys.argv.index("--trade")
        if idx + 1 < len(sys.argv):
            trade_filter = sys.argv[idx + 1].strip().lower()

    mode_label = "DRY RUN — no emails will be sent" if dry_run else "LIVE"
    print(f"\n=== TX Contract Intel — Batch Cold Email Sender ({mode_label}) ===\n")

    rows, fieldnames = load_prospects()
    if not rows:
        return

    # Filter to actionable prospects
    to_send = []
    for row in rows:
        status = (row.get("status") or "").strip().lower()
        email  = (row.get("email") or "").strip()
        trade  = (row.get("trade") or "IT staffing").strip()

        if status != "not_contacted":
            continue
        if not email:
            print(f"  ⏭  Skipping {row.get('company','?')} — no email address")
            continue
        if trade_filter and trade_filter not in trade.lower():
            continue
        to_send.append(row)

    if not to_send:
        print("No prospects with status 'not_contacted' and a valid email found.")
        return

    print(f"Found {len(to_send)} prospect(s) to contact:\n")

    sent_count   = 0
    skip_count   = 0
    failed_count = 0

    for row in to_send:
        company = row.get("company", "").strip()
        email   = row.get("email", "").strip()
        trade   = row.get("trade", "IT staffing").strip()
        contact = row.get("contact", row.get("notes", "")).strip()
        first_name = contact.split()[0] if contact and contact[0].isalpha() else company.split()[0]

        print(f"→ {company} ({email}) — trade: {trade}")

        contracts = fetch_matching_contracts(trade)
        if len(contracts) < MIN_CONTRACTS:
            print(f"  ⚠️  Only {len(contracts)} match(es) — skipping (need {MIN_CONTRACTS}+)")
            skip_count += 1
            continue

        subject, body = build_email(first_name, company, trade, contracts)

        print(f"  Subject: {subject}")
        for c in contracts:
            close = c['due_date'] or 'see listing'
            print(f"  • {c['title']} — {c['agency']}, closes {close}")

        if dry_run:
            print("  [DRY RUN] Would send — skipping actual send")
            sent_count += 1
            continue

        sent = send_via_resend(email, subject, body, company)
        if sent:
            mark_sent(rows, email)
            save_prospects(rows, fieldnames)
            sent_count += 1
        else:
            failed_count += 1

        time.sleep(SEND_DELAY_SECONDS)
        print()

    print("\n" + "=" * 55)
    print(f"  Sent:    {sent_count}")
    print(f"  Skipped: {skip_count}  (not enough contract matches)")
    print(f"  Failed:  {failed_count}  (Resend error)")
    if dry_run:
        print("\n  This was a dry run. Run without --dry-run to actually send.")
    print("=" * 55)


if __name__ == "__main__":
    main()
