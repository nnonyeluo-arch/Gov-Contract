# TX Contract Intel — Project Memory

## What This Is
B2B SaaS, $49/mo. Scrapes Texas government contract opportunities from 9 sources,
enriches them with AI, and sends a weekly Monday 7am CT email digest to subscribers.
Currently in early access.

---

## Business
- **Product name:** TX Contract Intel
- **Price:** $49/mo
- **Stripe checkout link:** https://buy.stripe.com/28EbITgGQ6iGanX9YDc7u00
- **Live site:** https://txcontractintel.com
- **Domain registrar:** (update when confirmed — user purchased based on my recommendation)
- **Target market:** Texas contractors, small businesses bidding on gov contracts

---

## Infrastructure
- **GitHub repo:** https://github.com/nnonyeluo-arch/Gov-Contract
  - Local clone: `~/Downloads/govcontract-intel`
  - Landing page: `docs/index.html` (served via GitHub Pages)
- **Supabase project ref:** `iswctzuithgbkxywtjsx`
  - Tables: `contracts`, `enriched_contracts`, `subscribers`, `scraper_logs`
  - `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are auto-injected by Supabase in GitHub Actions (cannot be set manually as secrets — name restriction)
- **Email:** Resend API (`RESEND_API_KEY` stored as GitHub Actions secret)
- **Enrichment:** Anthropic API (`ANTHROPIC_API_KEY` stored as GitHub Actions secret)

---

## GitHub Actions Workflows
- **`daily_scrape.yml`** — runs all scrapers daily, 45-min timeout, installs Playwright + Chromium
- **`send_digest.yml`** — runs every Monday, sends weekly email to all subscribers

### Secrets required in GitHub repo:
| Secret name | Value / Notes |
|---|---|
| `SAM_API_KEY` | SAM-16c5dad1-e984-462c-a173-6f30da135a60 |
| `RESEND_API_KEY` | (set) |
| `ANTHROPIC_API_KEY` | (set) |
| `SUPABASE_URL` | auto-injected |
| `SUPABASE_SERVICE_ROLE_KEY` | auto-injected |

---

## Scrapers — Status

| Source | File | Status | Notes |
|---|---|---|---|
| SAM.gov | `scrapers/sam_gov.py` | ⚠️ Rate limiting | SAM_API_KEY secret gets wiped when user clicks edit — DO NOT click pencil unless updating. Key: SAM-16c5dad1-e984-462c-a173-6f30da135a60 |
| Austin | `scrapers/austin.py` | ✅ Working | ColdFusion portal, Excel export returned 26 rows |
| San Antonio | `scrapers/san_antonio.py` | ✅ Working | ASP.NET portal, 2-page pagination |
| Travis County | `scrapers/travis_county.py` | ✅ Working | BidNet Direct |
| TxDOT | `scrapers/txdot.py` | ✅ Working | Div-based layout parser, NAICS defaults to 237310 |
| Fort Worth | `scrapers/fort_worth.py` | ⚠️ Partial | Bonfire SPA — Playwright intercept, getting some rows |
| Harris County | `scrapers/harris_county.py` | ⚠️ Pending | Migrated to Bonfire at harriscountytx.bonfirehub.com |
| TxSmartBuy (ESBD) | `scrapers/txsmartbuy.py` | ⚠️ Pending push | Div-based parser rewrite — needs cp + git push |
| Houston (Beacon) | `scrapers/houston.py` | ❌ 0 rows | Rewrote to call Beacon API directly at /api/ggf?operation=ListSolicitations — all params returning 403, needs session cookie. Pending push + test. |

### Pending actions:
1. Push `txsmartbuy.py` and `houston.py` and `digest/send_digest.py` (scraped_at fix)
2. HTTPS: add GitHub Pages A records at registrar, enable "Enforce HTTPS" in repo Settings → Pages
3. Welcome email on Stripe purchase — NOT BUILT YET
4. SAM_API_KEY — confirm it's set correctly in GitHub secrets (don't click edit/save unless actually updating)

---

## Digest Email
- **Recipient confirmed delivered:** nnonyeluo@yahoo.com (check junk/spam)
- **Date filter bug (fixed Jun 4):** moved from PostgREST join filter (broken) to Python-side filtering using `contract.scraped_at`
- **Sends:** Every Monday 7am CT via GitHub Actions cron

---

## Sales Outreach
- **Prospect list file:** `outputs/texas_gov_contract_prospects.md`
- **Call scripts file:** `outputs/cold_call_scripts.md`
- **Call 1:** Andy Smetana | PMCS Services | (512) 948-3144 | DIR@PMCSservices.com
- **Call 2:** Mariano Camarillo | Texas GovLink | (512) 474-1847 | mariano@texasgovlink.com
- **Call 3:** Allwin Insurance (LLC Monitor pitch) | (713) 952-5031
- **Best call time:** Tuesday/Wednesday 8:45am CT
- **Ask:** Free 30-day pilot → $49/mo after
- **Before calling:** Pull up txcontractintel.com, have email open to send sample digest immediately

---

## Digest Email
- **Recipient confirmed delivered:** nnonyeluo@yahoo.com (check junk/spam)
- **Subject used:** "100 New Contract Opportunities"
- **Date filter bug:** fixed — now uses `.gte("created_at", since)` + filters out expired due_dates
- **Sends:** Every Monday 7am CT via GitHub Actions cron

---

## Cowork Sandbox Path
Claude's working files live at:
`/Users/nnonyeluokafor/Library/Application Support/Claude/local-agent-mode-sessions/76a566a9-fb5a-4c11-9a1d-f83888e0c471/66f089f5-777c-49e5-995b-8820cdb932a1/agent/local_ditto_66f089f5-777c-49e5-995b-8820cdb932a1/outputs/govcontract-intel/`

Bash equivalent: `/sessions/optimistic-modest-goldberg/mnt/outputs/govcontract-intel/`

Files here are NOT in the git repo until the user runs the cp + push commands.

---

## Product Roadmap (agreed)
1. **Now:** Fix remaining scrapers (TxSmartBuy, Houston) → verify digest hits inbox → HTTPS on domain
2. **Pre-revenue:** Simple landing page (done) + Stripe checkout (done) + welcome email on purchase (NOT YET BUILT)
3. **After 3 paying customers:** Self-serve dashboard, NAICS filters
4. **After $500 MRR:** Permit Intel (second product)
5. **Later:** LLC Monitor

## Welcome Email
- **Status: NOT BUILT YET**
- Trigger: Stripe webhook → Supabase function or GitHub Action → Resend
- Tone: Early access, personal, sets expectations (Mondays 7am, reply with feedback)

---

## Security Rules (never violate)
- NEVER commit `.env` to GitHub
- SAM.gov API key, Supabase keys, Resend key — GitHub Actions secrets only
- GitHub PAT shared in earlier session — user should regenerate
