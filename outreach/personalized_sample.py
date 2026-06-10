"""
Personalized Contract Sample Email Generator
Usage: python personalized_sample.py

Prompts for company name + trade/niche, pulls 3 matching live contracts
from Supabase, and generates a personalized outreach email.
"""

import os
from datetime import date
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Map plain-English trade to category/keyword filters
TRADE_MAP = {
    "it":           {"categories": ["IT"], "keywords": ["technology", "software", "IT", "network", "cyber", "data", "system"]},
    "staffing":     {"categories": ["IT", "staffing"], "keywords": ["staffing", "augmentation", "staff", "personnel", "workforce"]},
    "construction": {"categories": ["construction"], "keywords": ["construction", "building", "renovation", "facility", "infrastructure", "road", "bridge"]},
    "janitorial":   {"categories": ["maintenance"], "keywords": ["janitorial", "cleaning", "custodial", "sanitation"]},
    "healthcare":   {"categories": ["healthcare"], "keywords": ["health", "medical", "clinical", "nursing", "hospital"]},
    "professional": {"categories": ["professional_services"], "keywords": ["consulting", "advisory", "management", "professional services"]},
}


def fetch_matching_contracts(trade: str, limit: int = 3) -> list[dict]:
    """Pull live enriched contracts matching the given trade."""
    today = date.today().isoformat()
    trade_lower = trade.lower().strip()

    # Find closest trade match
    trade_key = None
    for key in TRADE_MAP:
        if key in trade_lower or trade_lower in key:
            trade_key = key
            break

    # Build category filter
    categories = TRADE_MAP.get(trade_key, {}).get("categories", []) if trade_key else []

    try:
        # Pull enriched contracts with their contract data
        query = supabase.table("enriched_contracts")\
            .select("*, contracts(*)")\
            .order("complexity_score", desc=False)\
            .limit(200)\
            .execute()

        rows = query.data or []

        # Filter: not expired, matches trade
        keywords = TRADE_MAP.get(trade_key, {}).get("keywords", [trade_lower]) if trade_key else [trade_lower]
        matched = []

        for row in rows:
            contract = row.get("contracts") or {}
            if isinstance(contract, list):
                contract = contract[0] if contract else {}

            # Skip expired
            due = contract.get("due_date") or ""
            if due and due < today:
                continue

            # Match by category or keyword in title/summary
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
                    "complexity": row.get("complexity_score"),
                    "first_timer": row.get("first_timer_friendly", ""),
                })

            if len(matched) >= limit:
                break

        return matched[:limit]

    except Exception as e:
        print(f"Error fetching contracts: {e}")
        return []


def format_contract(c: dict, index: int) -> str:
    value_str = f"${c['value']:,.0f}" if c.get("value") else "Value TBD"
    due_str = c["due_date"] if c.get("due_date") else "See listing"
    complexity = f"Complexity {c['complexity']}/10" if c.get("complexity") else ""
    first_timer = "✓ First-timer friendly" if str(c.get("first_timer", "")).lower() == "yes" else ""
    tags = " · ".join(filter(None, [complexity, first_timer]))

    return f"""{index}. {c['title']}
   Agency: {c['agency']}
   Value: {value_str} · Due: {due_str}
   {c['summary'][:200] + '...' if len(c.get('summary','')) > 200 else c.get('summary','')}
   {tags}
   Link: {c['url']}"""


def generate_email(company: str, contact_name: str, trade: str, contracts: list[dict]) -> str:
    first_name = contact_name.split()[0] if contact_name else "there"
    trade_label = trade.title()

    contracts_text = "\n\n".join(format_contract(c, i+1) for i, c in enumerate(contracts))

    if not contracts:
        return f"No matching live contracts found for trade: {trade}. Try a broader keyword."

    email = f"""Subject: {len(contracts)} TX gov {trade_label} contracts closing soon — thought of {company}

Hey {first_name},

Saw these {len(contracts)} Texas government {trade_label} opportunities close this month that look like a fit for {company}:

{contracts_text}

I built a platform that pulls contracts like these from every Texas source daily — SmartBuy, SAM.gov, city portals, TxDOT — and sends a filtered digest every Monday morning so you never miss one.

I'm looking for a few Texas {trade_label} firms to use it free for 30 days. Would this be useful for {company}?

— King Okafor
txcontractintel.com
"""
    return email.strip()


def main():
    print("=== TX Contract Intel — Personalized Sample Generator ===\n")
    company = input("Company name: ").strip()
    contact = input("Contact name (first last): ").strip()
    trade = input("Trade/niche (IT / staffing / construction / janitorial / healthcare / professional): ").strip()

    print(f"\nSearching for live {trade} contracts matching {company}...")
    contracts = fetch_matching_contracts(trade)

    if not contracts:
        print(f"\n⚠️  No matching contracts found for '{trade}'. Try: IT, staffing, construction, janitorial, healthcare, professional")
        return

    email = generate_email(company, contact, trade, contracts)

    print("\n" + "="*60)
    print("PERSONALIZED EMAIL — copy and send this:")
    print("="*60 + "\n")
    print(email)
    print("\n" + "="*60)
    print(f"✅ Generated {len(contracts)} contract matches for {company}")


if __name__ == "__main__":
    main()
