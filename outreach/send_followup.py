"""
TX Contract Intel — Day-4 Follow-Up Sender
Usage: python send_followup.py

Reads prospects.csv, finds everyone with status "sent" and a sent_date
4+ days ago, and sends the follow-up email via Resend.

Rules:
  - One follow-up per prospect only (status must be exactly "sent")
  - Any status other than "sent" is skipped (replied, followup_sent, cold, etc.)
  - Updates CSV status to "followup_sent" after each send

Requires env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  RESEND_API_KEY
"""

import os
import csv
import httpx
import pathlib
import tempfile
import shutil
from datetime import date, timedelta
from supabase import create_client, Client

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PROSPECTS_CSV = pathlib.Path(__file__).parent / "prospects.csv"
FOLLOWUP_DELAY_DAYS = 4


# ── fetch the top contract from the original send to reference in follow-up ──

def get_top_contract(trade: str) -> dict | None:
    today = date.today().isoformat()
    trade_lower = trade.lower()

    TRADE_KEYWORDS = {
        "it":           ["technology", "software", "IT", "network", "cyber"],
        "staffing":     ["staffing", "augmentation", "staff", "personnel"],
        "construction": ["construction", "building", "renovation", "facility"],
        "janitorial":   ["janitorial", "cleaning", "custodial", "sanitation"],
        "healthcare":   ["health", "medical", "clinical", "nursing"],
        "professional": ["consulting", "advisory", "management"],
        "roofing":      ["roof", "roofing", "waterproof"],
        "security":     ["security", "guard", "surveillance"],
    }

    trade_key = next((k for k in TRADE_KEYWORDS if k in trade_lower or trade_lower in k), None)
    keywords = TRADE_KEYWORDS.get(trade_key, [trade_lower]) if trade_key else [trade_lower]

    try:
        result = supabase.table("enriched_contracts")\
            .select("*, contracts(*)")\
            .limit(200)\
            .execute()

        for row in result.data or []:
            contract = row.get("contracts") or {}
            if isinstance(contract, list):
                contract = contract[0] if contract else {}

            due = contract.get("due_date") or ""
            if due and due < today:
                continue

            title = (contract.get("title") or "").lower()
            summary = (row.get("summary") or "").lower()

            if any(kw.lower() in title or kw.lower() in summary for kw in keywords):
                return {
                    "title": contract.get("title", "Contract Opportunity"),
                    "agency": contract.get("agency", "Texas Agency"),
                    "due_date": due,
                }
    except Exception as e:
        print(f"  Warning: could not fetch contract for follow-up: {e}")

    return None


# ── email ─────────────────────────────────────────────────────────────────────

def build_followup(first_name: str, company: str, trade: str, contract: dict | None) -> tuple[str, str]:
    trade_label = trade.title()
    subject = f"Re: 3 Texas {trade_label} bids closing this month"

    if contract:
        agency = contract["agency"]
        close  = contract["due_date"] if contract.get("due_date") else "soon"
        ref_line = f"Quick follow-up. The {agency} bid I sent closes {close}, so flagging it before the window shrinks."
    else:
        ref_line = "Quick follow-up on the Texas contract opportunities I sent over."

    body = f"""{first_name},

{ref_line}

If the matches were off, tell me what {company} actually bids on and I'll send a corrected set. Takes me two minutes.

King"""

    return subject, body


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
                "from": "King Okafor <okafor@txcontractintel.com>",
                "to": [to_email],
                "subject": subject,
                "text": body,
                "tags": [
                    {"name": "campaign", "value": "followup"},
                    {"name": "company",  "value": safe_company},
                ],
            },
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        print(f"  ✅ Sent — Resend ID: {result.get('id')}")
        return True
    except httpx.HTTPStatusError as e:
        print(f"  ❌ Resend {e.response.status_code}: {e.response.text}")
        return False
    except Exception as e:
        print(f"  ❌ Send failed: {e}")
        return False


# ── CSV update ────────────────────────────────────────────────────────────────

def update_csv_status(email: str, new_status: str) -> None:
    """Rewrite the CSV with the updated status for the given email."""
    rows = []
    with open(PROSPECTS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row["email"].strip().lower() == email.strip().lower():
                row["status"] = new_status
            rows.append(row)

    tmp = pathlib.Path(tempfile.mktemp())
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    shutil.move(str(tmp), str(PROSPECTS_CSV))


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if not PROSPECTS_CSV.exists():
        print("prospects.csv not found. Run send_cold_email.py first.")
        return

    cutoff = (date.today() - timedelta(days=FOLLOWUP_DELAY_DAYS)).isoformat()
    to_follow_up = []

    with open(PROSPECTS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            status    = (row.get("status") or "").strip().lower()
            sent_date = (row.get("sent_date") or "").strip()
            email     = (row.get("email") or "").strip()

            if status != "sent":
                continue
            if not email or not sent_date:
                continue
            if sent_date > cutoff:  # not yet 4 days
                continue

            to_follow_up.append(row)

    if not to_follow_up:
        print("No prospects due for follow-up today.")
        return

    print(f"=== TX Contract Intel — Day-4 Follow-Up Sender ===\n")
    print(f"Found {len(to_follow_up)} prospect(s) to follow up:\n")

    sent_count = 0
    for row in to_follow_up:
        company   = row.get("company", "").strip()
        email     = row.get("email", "").strip()
        trade     = row.get("trade", "IT staffing").strip()
        # Best-guess first name from notes or just use company
        first_name = company.split()[0]

        print(f"→ {company} ({email})")

        contract = get_top_contract(trade)
        subject, body = build_followup(first_name, company, trade, contract)

        sent = send_via_resend(email, subject, body, company)
        if sent:
            update_csv_status(email, "followup_sent")
            sent_count += 1

    print(f"\n✅ Follow-ups sent: {sent_count}/{len(to_follow_up)}")
    print("\nAnyone who replies: update their status in prospects.csv to 'replied'")
    print("and send them the full Monday digest manually as the demo.")


if __name__ == "__main__":
    main()
