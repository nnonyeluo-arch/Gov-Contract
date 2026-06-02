"""
Run all scrapers sequentially.
Individual scraper failures are logged but do NOT stop the pipeline —
enrichment will still run on whatever contracts were successfully scraped.
"""

import sys
import time

SCRAPERS = [
    ("txsmartbuy",  "txsmartbuy"),
    ("sam_gov",     "sam_gov"),
    ("houston",     "houston"),
    ("austin",      "austin"),
    ("san_antonio", "san_antonio"),
]

results = {}

for name, module_name in SCRAPERS:
    print(f"\n{'='*50}")
    print(f"Running scraper: {name}")
    print('='*50)
    try:
        mod = __import__(module_name)
        mod.run()
        results[name] = "ok"
    except Exception as e:
        print(f"[run_all] FAILED scraper '{name}': {e}")
        results[name] = f"error: {e}"

print("\n" + "="*50)
print("SCRAPER SUMMARY")
print("="*50)
for name, status in results.items():
    icon = "✓" if status == "ok" else "✗"
    print(f"  {icon} {name}: {status}")

failed = [n for n, s in results.items() if s != "ok"]
if failed:
    print(f"\n[run_all] {len(failed)} scraper(s) had errors: {', '.join(failed)}")
    print("[run_all] Enrichment will still run on successfully scraped contracts.")
    # Exit 0 so GitHub Actions continues to the enrichment step
    # Change to sys.exit(1) if you want the job to fail when ANY scraper fails
    sys.exit(0)
else:
    print("\n[run_all] All scrapers completed successfully.")
    sys.exit(0)
