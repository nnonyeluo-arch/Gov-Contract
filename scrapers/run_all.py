"""
Master runner — executes all scrapers in sequence.
Called by GitHub Actions cron job daily at 6am CT.
"""

import sys
import traceback

from txsmartbuy import run as run_txsmartbuy
from sam_gov import run as run_sam_gov
from houston import run as run_houston

SCRAPERS = [
    ("txsmartbuy", run_txsmartbuy),
    ("sam_gov", run_sam_gov),
    ("houston", run_houston),
]

def main():
    failures = []
    for name, runner in SCRAPERS:
        try:
            print(f"\n{'='*50}")
            print(f"Running scraper: {name}")
            print(f"{'='*50}")
            runner()
        except Exception as e:
            print(f"[{name}] FAILED: {e}")
            traceback.print_exc()
            failures.append(name)

    if failures:
        print(f"\n❌ Failed scrapers: {', '.join(failures)}")
        sys.exit(1)
    else:
        print("\n✅ All scrapers completed successfully")


if __name__ == "__main__":
    main()
