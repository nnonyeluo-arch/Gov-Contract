"""
Phase 2 — AI Enrichment Worker
Pulls unprocessed contracts from Supabase, sends each through Claude API (Haiku),
extracts structured intel, stores in enriched_contracts.
Caches by content hash — won't reprocess unchanged listings.
"""

import os
import json
import hashlib
import time
from supabase import create_client, Client
import anthropic

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

BATCH_SIZE = 50   # contracts per run
SLEEP_BETWEEN = 0.3  # seconds between API calls (rate limiting)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


ENRICHMENT_PROMPT = """You are analyzing a government contract opportunity for small Texas contractors.

Contract details:
Title: {title}
Agency: {agency}
NAICS Code: {naics}
Estimated Value: {value}
Due Date: {due_date}
Set-Aside: {set_aside}
Additional Info: {raw_snippet}

Respond with ONLY valid JSON in this exact format (no markdown, no explanation):
{{
  "summary": "2-3 sentence plain English description of what this contract is for and what the winning contractor will need to do",
  "category": "one of: IT | construction | staffing | healthcare | professional_services | logistics | maintenance | other",
  "complexity_score": <integer 1-10 where 1=simple/easy, 10=highly complex>,
  "first_time_friendly": "yes | no | maybe",
  "first_time_reasoning": "1 sentence explaining why this is or isn't good for first-time government bidders"
}}"""


def get_content_hash(contract: dict) -> str:
    """Hash key contract fields to detect changes."""
    key = f"{contract.get('title', '')}{contract.get('value', '')}{contract.get('due_date', '')}{contract.get('raw_html', '')[:500]}"
    return hashlib.md5(key.encode()).hexdigest()


def fetch_unprocessed(limit: int = BATCH_SIZE) -> list[dict]:
    """Get contracts that haven't been enriched yet."""
    result = supabase.rpc("get_unenriched_contracts", {"batch_limit": limit}).execute()

    # Fallback if RPC doesn't exist yet — use direct query
    if not result.data:
        enriched_ids = supabase.table("enriched_contracts").select("contract_id").execute()
        done_ids = [r["contract_id"] for r in (enriched_ids.data or [])]

        query = supabase.table("contracts").select("*").limit(limit)
        if done_ids:
            query = query.not_.in_("id", done_ids)
        result = query.execute()

    return result.data or []


def enrich_contract(contract: dict) -> dict | None:
    """Call Claude Haiku to extract structured intel from a contract."""
    raw_snippet = ""
    if contract.get("raw_html"):
        # Strip HTML tags roughly, take first 500 chars
        import re
        raw_snippet = re.sub(r'<[^>]+>', ' ', contract["raw_html"])[:500].strip()

    prompt = ENRICHMENT_PROMPT.format(
        title=contract.get("title", "Unknown"),
        agency=contract.get("agency", "Unknown agency"),
        naics=contract.get("naics", "Not specified"),
        value=f"${contract['value']:,.0f}" if contract.get("value") else "Not specified",
        due_date=contract.get("due_date", "Not specified"),
        set_aside=contract.get("set_aside", "None"),
        raw_snippet=raw_snippet or "No additional details available",
    )

    try:
        message = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()

        # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]  # get content between first and second ```
            if raw.startswith("json"):
                raw = raw[4:]             # strip the "json" language tag
            raw = raw.strip()
            # Remove trailing ``` if present
            if raw.endswith("```"):
                raw = raw[:-3].strip()

        # Parse JSON response
        data = json.loads(raw)

        return {
            "contract_id": contract["id"],
            "summary": data.get("summary", ""),
            "category": data.get("category", "other"),
            "complexity_score": int(data.get("complexity_score", 5)),
            "first_time_friendly": data.get("first_time_friendly", "maybe"),
            "first_time_reasoning": data.get("first_time_reasoning", ""),
            "match_tags": extract_tags(data, contract),
            "content_hash": get_content_hash(contract),
        }

    except json.JSONDecodeError as e:
        print(f"[enrich] JSON parse error for {contract['id']}: {e}")
        print(f"[enrich] Raw response: {raw[:200]}")
        return None
    except Exception as e:
        print(f"[enrich] API error for {contract['id']}: {e}")
        return None


def extract_tags(data: dict, contract: dict) -> list[str]:
    """Generate match tags for filtering by client niche."""
    tags = []
    category = data.get("category", "other")
    tags.append(category)

    title_lower = (contract.get("title") or "").lower()
    naics = contract.get("naics", "") or ""

    # IT tags
    if any(w in title_lower for w in ["software", "it ", "technology", "cyber", "cloud", "data", "network", "system"]):
        tags.append("IT")
    # Construction tags
    if any(w in title_lower for w in ["construction", "renovati", "repair", "build", "facility", "infrastructure"]):
        tags.append("construction")
    # Staffing tags
    if any(w in title_lower for w in ["staffing", "staff augmentation", "personnel", "temporary", "workforce"]):
        tags.append("staffing")
    # Small business
    if contract.get("set_aside") and "small" in (contract.get("set_aside") or "").lower():
        tags.append("small_business_set_aside")
    # High value
    if contract.get("value") and contract["value"] >= 100000:
        tags.append("high_value")

    return list(set(tags))


def upsert_enrichment(enriched: dict) -> bool:
    """Save enrichment result to Supabase."""
    try:
        supabase.table("enriched_contracts").upsert(
            enriched,
            on_conflict="contract_id"
        ).execute()
        return True
    except Exception as e:
        print(f"[enrich] DB error saving {enriched['contract_id']}: {e}")
        return False


def check_hash_changed(contract_id: str, new_hash: str) -> bool:
    """Return True if contract content changed (needs reprocessing)."""
    result = supabase.table("enriched_contracts")\
        .select("content_hash")\
        .eq("contract_id", contract_id)\
        .execute()

    if not result.data:
        return True  # not processed yet

    existing_hash = result.data[0].get("content_hash")
    return existing_hash != new_hash


def run():
    start = time.time()
    print("[enrich] Starting enrichment run...")

    contracts = fetch_unprocessed(BATCH_SIZE)
    print(f"[enrich] Found {len(contracts)} contracts to process")

    processed = 0
    skipped = 0
    errors = 0

    for contract in contracts:
        contract_id = contract["id"]
        new_hash = get_content_hash(contract)

        # Skip if content hasn't changed
        if not check_hash_changed(contract_id, new_hash):
            skipped += 1
            continue

        print(f"[enrich] Processing: {contract.get('title', 'Unknown')[:60]}...")
        enriched = enrich_contract(contract)

        if enriched:
            if upsert_enrichment(enriched):
                processed += 1
                print(f"[enrich] ✓ {contract.get('title', '')[:50]} → {enriched['category']} | score: {enriched['complexity_score']} | {enriched['first_time_friendly']}")
        else:
            errors += 1

        time.sleep(SLEEP_BETWEEN)

    duration = int((time.time() - start) * 1000)
    print(f"\n[enrich] Done. Processed: {processed}, Skipped (unchanged): {skipped}, Errors: {errors}, Time: {duration}ms")

    # Log to scraper_logs table
    supabase.table("scraper_logs").insert({
        "source": "enrichment",
        "status": "success" if errors == 0 else "partial",
        "contracts_found": len(contracts),
        "contracts_new": processed,
        "error_message": f"{errors} enrichment errors" if errors else None,
        "duration_ms": duration,
    }).execute()


if __name__ == "__main__":
    run()
