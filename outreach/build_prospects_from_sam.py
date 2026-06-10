"""
TX Contract Intel — SAM.gov Prospect Builder
Usage: python build_prospects_from_sam.py

Queries SAM.gov Entity API for active Texas IT/staffing vendors,
extracts contact info, and appends new rows to prospects.csv.

Skips companies already in prospects.csv (matched by name or email).

Requires env var:
  SAM_API_KEY   (e.g. SAM-16c5dad1-...)

Optional env var:
  NAICS         comma-separated list of NAICS codes to search (default below)
  CITY          filter to a specific Texas city (e.g. "Austin")
  MAX_RESULTS   max prospects to pull per NAICS code (default: 50)
"""

import os
import csv
import time
import httpx
import pathlib

SAM_API_KEY  = os.environ.get("SAM_API_KEY", "")
PROSPECTS_CSV = pathlib.Path(__file__).parent / "prospects.csv"

# NAICS codes to target — IT services + staffing
DEFAULT_NAICS = [
    "541512",  # Computer Systems Design Services
    "541511",  # Custom Computer Programming Services
    "541519",  # Other Computer Related Services
    "561320",  # Temporary Help Services (IT staffing)
    "541330",  # Engineering Services
    "541690",  # Other Scientific & Technical Consulting
    "541990",  # All Other Professional, Scientific, Technical Services
]

MAX_RESULTS  = int(os.environ.get("MAX_RESULTS", "50"))
CITY_FILTER  = (os.environ.get("CITY") or "").strip().upper()
NAICS_CODES  = [n.strip() for n in (os.environ.get("NAICS") or ",".join(DEFAULT_NAICS)).split(",")]

SAM_BASE     = "https://api.sam.gov/entity-information/v3/entities"


# ── Load existing prospects to avoid duplicates ───────────────────────────────

def load_existing() -> tuple[set, set, list, list]:
    """Returns (existing_emails, existing_companies, rows, fieldnames)"""
    if not PROSPECTS_CSV.exists():
        return set(), set(), [], []
    with open(PROSPECTS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    emails    = {r.get("email","").strip().lower() for r in rows if r.get("email")}
    companies = {r.get("company","").strip().lower() for r in rows if r.get("company")}
    return emails, companies, rows, fieldnames


# ── SAM.gov Entity API ────────────────────────────────────────────────────────

def fetch_sam_entities(naics: str, page: int = 0) -> dict:
    # v3 API correct parameter names (per https://open.gsa.gov/api/entity-api/)
    params = {
        "api_key":                          SAM_API_KEY,
        "physicalAddressProvinceOrStateCode": "TX",
        "naicsCode":                        naics,
        "registrationStatus":               "A",     # A = Active
        "purposeOfRegistrationCode":        "Z2",    # All Awards — they want gov work
        "includeSections":                  "entityRegistration,coreData,assertions,pointsOfContact",
        "page":                             page,    # API returns 10 records per page
    }
    if CITY_FILTER:
        params["physicalAddressCity"] = CITY_FILTER

    try:
        resp = httpx.get(SAM_BASE, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        print(f"  ❌  SAM.gov API error {e.response.status_code}: {e.response.text[:200]}")
        return {}
    except Exception as e:
        print(f"  ❌  Request error: {e}")
        return {}


def extract_contact(entity: dict) -> dict | None:
    """Pull the best available contact from an entity record."""
    reg  = entity.get("entityRegistration") or {}
    core = entity.get("coreData") or {}
    poc  = entity.get("pointsOfContact") or {}

    company = (reg.get("legalBusinessName") or "").strip()
    if not company:
        return None

    # Try POC types in order of preference
    contact_info = None
    for poc_type in ["electronicBusinessPOC", "governmentBusinessPOC", "pastPerformancePOC"]:
        p = poc.get(poc_type) or {}
        first = (p.get("firstName") or "").strip()
        last  = (p.get("lastName")  or "").strip()
        email = (p.get("email") or p.get("emailAddress") or "").strip().lower()
        phone = (p.get("phoneNumber") or "").strip()
        if first or email:
            contact_info = {
                "name":  f"{first} {last}".strip(),
                "email": email,
                "phone": phone,
            }
            break

    if not contact_info:
        return None

    # Address
    addr   = (core.get("physicalAddress") or core.get("mailingAddress") or {})
    city   = (addr.get("city") or "").strip().title()
    state  = (addr.get("stateOrProvinceCode") or "TX").strip()

    # Business description for trade inference
    naics_list = []
    assertions = entity.get("assertions") or {}
    goods      = assertions.get("goodsAndServices") or {}
    for n in goods.get("naicsList") or []:
        code = str(n.get("naicsCode") or "")
        desc = (n.get("naicsDescription") or "").lower()
        if code:
            naics_list.append((code, desc))

    # Infer trade from primary NAICS
    trade = "IT staffing"
    for code, desc in naics_list:
        if code.startswith("5613"):
            trade = "staffing"
            break
        elif code in ("541512", "541511", "541519"):
            trade = "IT"
            break
        elif code.startswith("2362") or code.startswith("2361"):
            trade = "construction"
            break

    return {
        "company": company,
        "contact": contact_info["name"],
        "email":   contact_info["email"],
        "trade":   trade,
        "city":    city,
        "phone":   contact_info["phone"],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not SAM_API_KEY:
        print("❌  SAM_API_KEY env var not set.")
        print("    export SAM_API_KEY=SAM-16c5dad1-e984-462c-a173-6f30da135a60")
        return

    print(f"\n=== TX Contract Intel — SAM.gov Prospect Builder ===")
    print(f"NAICS codes: {', '.join(NAICS_CODES)}")
    print(f"Max per code: {MAX_RESULTS}")
    if CITY_FILTER:
        print(f"City filter: {CITY_FILTER}")
    print()

    existing_emails, existing_companies, rows, fieldnames = load_existing()
    print(f"Existing prospects in CSV: {len(rows)}\n")

    # Ensure fieldnames include all columns we write
    needed = ["company", "contact", "email", "trade", "sent_date", "status", "notes"]
    if not fieldnames:
        fieldnames = needed
    for col in needed:
        if col not in fieldnames:
            fieldnames.append(col)

    new_prospects = []

    for naics in NAICS_CODES:
        print(f"→ Searching NAICS {naics}...")
        collected = 0
        page      = 0

        while collected < MAX_RESULTS:
            data = fetch_sam_entities(naics, page)
            entities = data.get("entityData") or []

            if not entities:
                break

            for entity in entities:
                if collected >= MAX_RESULTS:
                    break

                result = extract_contact(entity)
                if not result:
                    continue

                email   = result["email"].lower()
                company = result["company"].lower()

                # Skip if no email
                if not email or "@" not in email:
                    continue

                # Skip duplicates
                if email in existing_emails or company in existing_companies:
                    continue

                # Skip obvious generic emails
                if any(x in email for x in ["info@", "admin@", "contact@", "noreply@", "no-reply@"]):
                    continue

                new_prospects.append(result)
                existing_emails.add(email)
                existing_companies.add(company)
                collected += 1

            total = data.get("totalRecords", 0)
            # SAM.gov API returns 10 records per page
            fetched_so_far = (page + 1) * 10
            if fetched_so_far >= total or collected >= MAX_RESULTS:
                break
            page += 1
            time.sleep(0.5)  # respect rate limits

        print(f"   Found {collected} new prospects for NAICS {naics}")

    if not new_prospects:
        print("\nNo new prospects found (all may already be in prospects.csv).")
        return

    # Append to CSV
    print(f"\nAdding {len(new_prospects)} new prospects to prospects.csv...\n")

    # Show preview
    for p in new_prospects[:5]:
        print(f"  {p['company']} | {p['contact']} | {p['email']} | {p['trade']} | {p.get('city','')}")
    if len(new_prospects) > 5:
        print(f"  ... and {len(new_prospects) - 5} more")

    new_rows = []
    for p in new_prospects:
        city_note = f"City: {p['city']}" if p.get("city") else ""
        phone_note = f"Phone: {p['phone']}" if p.get("phone") else ""
        notes = " | ".join(filter(None, [city_note, phone_note, "SAM.gov entity"]))
        new_rows.append({
            "company":   p["company"],
            "contact":   p["contact"],
            "email":     p["email"],
            "trade":     p["trade"],
            "sent_date": "",
            "status":    "not_contacted",
            "notes":     notes,
        })

    all_rows = rows + new_rows
    write_header = not PROSPECTS_CSV.exists()

    with open(PROSPECTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n✅  prospects.csv updated — {len(new_rows)} new prospects added ({len(all_rows)} total)")
    print("\nNext steps:")
    print("  1. Review prospects.csv and remove any bad contacts")
    print("  2. python outreach/send_batch.py --dry-run   (preview)")
    print("  3. python outreach/send_batch.py             (send)")


if __name__ == "__main__":
    main()
