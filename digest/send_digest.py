"""
Phase 3 — Weekly Email Digest
Pulls enriched contracts from the past 7 days, groups by category,
builds an HTML email, and sends via Resend to all active clients.
Runs every Monday at 7am CT via GitHub Actions.
"""

import os
import json
import resend
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
FROM_EMAIL = os.environ.get("FROM_EMAIL", "intel@txcontractintel.com")
FROM_NAME = os.environ.get("FROM_NAME", "TX Contract Intel")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
resend.api_key = RESEND_API_KEY

CATEGORY_LABELS = {
    "IT":                    "💻 IT & Technology",
    "construction":          "🏗️ Construction & Facilities",
    "staffing":              "👥 Staffing & Workforce",
    "healthcare":            "🏥 Healthcare",
    "professional_services": "📋 Professional Services",
    "logistics":             "🚚 Logistics & Supply Chain",
    "maintenance":           "🔧 Maintenance & Repair",
    "other":                 "📌 Other Opportunities",
}

FRIENDLY_LABELS = {
    "yes":   "✅ First-timer friendly",
    "no":    "⚠️ Experienced vendors only",
    "maybe": "🔄 Case by case",
}


def fetch_recent_contracts(days: int = 7) -> list[dict]:
    """Pull enriched contracts from the past N days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    result = supabase.table("enriched_contracts")\
        .select("*, contracts(*)")\
        .order("complexity_score", desc=False)\
        .limit(100)\
        .execute()

    return result.data or []


def fetch_clients() -> list[dict]:
    """Get all active clients to send digests to."""
    result = supabase.table("clients")\
        .select("*")\
        .eq("active", True)\
        .execute()
    return result.data or []


def group_by_category(enriched: list[dict]) -> dict:
    """Group enriched contracts by category."""
    groups = {}
    for row in enriched:
        cat = row.get("category") or "other"
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(row)
    return groups


def filter_for_client(enriched: list[dict], client: dict) -> list[dict]:
    """Filter contracts relevant to a client based on their niche tags."""
    niches = client.get("niches") or []
    if not niches:
        return enriched  # no filter = send everything

    filtered = []
    for row in enriched:
        tags = row.get("match_tags") or []
        if any(n in tags for n in niches):
            filtered.append(row)
    return filtered


def format_contract_card(row: dict) -> str:
    """Render a single contract as an HTML card."""
    # Supabase may return the joined table as a list or dict depending on version
    contract = row.get("contracts") or {}
    if isinstance(contract, list):
        contract = contract[0] if contract else {}

    title = (contract.get("title") or "Untitled Contract")[:100]
    agency = contract.get("agency") or ""
    due_date = contract.get("due_date") or "TBD"
    value = contract.get("value")
    url = contract.get("url") or "#"
    set_aside = contract.get("set_aside") or ""

    summary = row.get("summary") or ""
    score = row.get("complexity_score") or 5
    friendly = row.get("first_time_friendly") or "maybe"
    reasoning = row.get("first_time_reasoning") or ""

    value_str = f"${value:,.0f}" if value else "Not listed"
    agency_str = agency[:80] if agency else "See listing"
    score_color = "#22c55e" if score <= 3 else "#f59e0b" if score <= 6 else "#ef4444"
    set_aside_tag = f'<span style="background:#dbeafe;color:#1d4ed8;padding:2px 8px;border-radius:12px;font-size:11px;white-space:nowrap;">{set_aside[:50]}</span>' if set_aside else ""
    reasoning_html = f'<p style="color:#64748b;font-size:12px;margin:6px 0 0 0;font-style:italic;">{reasoning}</p>' if reasoning else ""

    return f"""
    <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px;margin-bottom:14px;">

      <!-- Title + agency -->
      <a href="{url}" style="color:#60a5fa;font-size:14px;font-weight:600;text-decoration:none;line-height:1.4;display:block;">{title}</a>
      <div style="color:#94a3b8;font-size:12px;margin-top:3px;">{agency_str}</div>

      <!-- Value + due date row -->
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px;margin-bottom:10px;">
        <tr>
          <td style="color:#f1f5f9;font-size:13px;font-weight:600;">💰 {value_str}</td>
          <td style="color:#94a3b8;font-size:12px;text-align:right;">Due: {due_date}</td>
        </tr>
      </table>

      <!-- Summary -->
      <p style="color:#cbd5e1;font-size:13px;line-height:1.5;margin:0 0 10px 0;">{summary}</p>

      <!-- Tags -->
      <div style="margin-bottom:8px;">
        <span style="background:#0f172a;border:1px solid #475569;color:#94a3b8;padding:2px 10px;border-radius:12px;font-size:11px;margin-right:6px;">Complexity: <span style="color:{score_color};font-weight:600;">{score}/10</span></span>
        <span style="background:#0f172a;border:1px solid #475569;color:#94a3b8;padding:2px 10px;border-radius:12px;font-size:11px;margin-right:6px;">{FRIENDLY_LABELS.get(friendly, friendly)}</span>
        {set_aside_tag}
      </div>

      {reasoning_html}

      <a href="{url}" style="display:inline-block;margin-top:10px;background:#3b82f6;color:#fff;padding:6px 14px;border-radius:6px;font-size:12px;font-weight:500;text-decoration:none;">View Opportunity →</a>
    </div>
    """


def build_email_html(enriched: list[dict], client: dict, week_str: str) -> str:
    """Build the full HTML email for a client."""
    company = client.get("company") or client.get("name") or ""
    first_name = company.split()[0] if company else "there"
    groups = group_by_category(enriched)
    total = len(enriched)

    # Build table of contents
    toc_rows = ""
    sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]))
    for cat, rows in sorted_groups:
        label = CATEGORY_LABELS.get(cat, cat.replace("_", " ").title())
        toc_rows += f"""
        <tr>
          <td style="padding:6px 0;border-bottom:1px solid #1e293b;">
            <a href="#{cat}" style="color:#60a5fa;text-decoration:none;font-size:13px;">{label}</a>
          </td>
          <td style="padding:6px 0;border-bottom:1px solid #1e293b;text-align:right;color:#64748b;font-size:13px;">{len(rows)} opportunities</td>
        </tr>"""

    # Build contract sections with anchor IDs
    sections_html = ""
    for cat, rows in sorted_groups:
        label = CATEGORY_LABELS.get(cat, cat.replace("_", " ").title())
        cards = "".join(format_contract_card(r) for r in rows)
        sections_html += f"""
        <div id="{cat}" style="margin-bottom:32px;">
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
            <tr>
              <td style="color:#f1f5f9;font-size:17px;font-weight:600;padding-bottom:8px;border-bottom:1px solid #334155;">
                {label}
              </td>
              <td style="color:#64748b;font-size:13px;text-align:right;padding-bottom:8px;border-bottom:1px solid #334155;white-space:nowrap;">
                {len(rows)} opportunities
              </td>
            </tr>
          </table>
          {cards}
          <a href="#toc" style="color:#475569;font-size:12px;text-decoration:none;">↑ Back to top</a>
        </div>
        """

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:660px;margin:0 auto;padding:24px 16px;">

    <!-- Header -->
    <div id="toc" style="text-align:center;margin-bottom:24px;">
      <div style="color:#3b82f6;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px;">TX CONTRACT INTEL</div>
      <h1 style="color:#f1f5f9;font-size:22px;font-weight:700;margin:0 0 4px 0;">Your Weekly Contract Digest</h1>
      <div style="color:#64748b;font-size:13px;">Week of {week_str}</div>
    </div>

    <!-- Summary bar (stacked, mobile-safe) -->
    <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px 20px;margin-bottom:20px;text-align:center;">
      <div style="color:#f1f5f9;font-size:28px;font-weight:700;line-height:1;">{total}</div>
      <div style="color:#64748b;font-size:13px;margin-top:4px;">new opportunities this week, {first_name}</div>
    </div>

    <!-- Table of Contents -->
    <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px 20px;margin-bottom:28px;">
      <div style="color:#94a3b8;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">Jump to category</div>
      <table width="100%" cellpadding="0" cellspacing="0">
        {toc_rows}
      </table>
    </div>

    <!-- Contract sections -->
    {sections_html}

    <!-- Footer -->
    <div style="border-top:1px solid #1e293b;padding-top:20px;text-align:center;">
      <p style="color:#475569;font-size:12px;margin:0 0 6px 0;">
        You're receiving this because you subscribed to TX Contract Intel.
      </p>
      <p style="color:#334155;font-size:12px;margin:0;">
        Questions? Reply to this email. · TX Contract Intel · Texas
      </p>
    </div>

  </div>
</body>
</html>
    """


def log_delivery(client_id: str, email: str, count: int, status: str, error: str = None):
    """Log digest delivery to Supabase."""
    try:
      supabase.table("deliveries").insert({
        "client_id": client_id,
        "email": email,
        "contracts_sent": count,
        "status": status,
        "error_message": error,
      }).execute()
    except Exception as e:
      print(f"[digest] Warning: could not log delivery to DB: {e}")


def run():
    import time
    start = time.time()
    print("[digest] Starting weekly digest run...")

    week_str = datetime.now().strftime("%B %d, %Y")
    enriched = fetch_recent_contracts(days=7)
    print(f"[digest] Found {len(enriched)} enriched contracts from the past 7 days")

    if not enriched:
        print("[digest] No contracts to send. Exiting.")
        return

    clients = fetch_clients()
    print(f"[digest] Sending to {len(clients)} active clients")

    sent = 0
    errors = 0

    for client in clients:
        client_id = client["id"]
        email = client.get("email")
        name = client.get("name") or email

        if not email:
            print(f"[digest] Skipping client {client_id} — no email")
            continue

        filtered = filter_for_client(enriched, client)
        if not filtered:
            print(f"[digest] No matching contracts for {name} — skipping")
            continue

        html = build_email_html(filtered, client, week_str)

        try:
            result = resend.Emails.send({
                "from": f"{FROM_NAME} <{FROM_EMAIL}>",
                "to": email,
                "subject": f"📋 {len(filtered)} New Contract Opportunities — Week of {week_str}",
                "html": html,
            })
            print(f"[digest] ✓ Sent to {name} ({email}) — {len(filtered)} contracts | id: {result.get('id', '?')}")
            log_delivery(client_id, email, len(filtered), "sent")
            sent += 1
        except Exception as e:
            print(f"[digest] ✗ Failed to send to {name} ({email}): {e}")
            log_delivery(client_id, email, 0, "error", str(e))
            errors += 1

        time.sleep(0.5)  # rate limit Resend

    duration = int((time.time() - start) * 1000)
    print(f"\n[digest] Done. Sent: {sent}, Errors: {errors}, Time: {duration}ms")

    supabase.table("scraper_logs").insert({
        "source": "digest",
        "status": "success" if errors == 0 else "partial",
        "contracts_found": len(enriched),
        "contracts_new": sent,
        "error_message": f"{errors} send errors" if errors else None,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
