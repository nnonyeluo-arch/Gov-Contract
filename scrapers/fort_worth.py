"""City of Fort Worth — Bonfire/Euna Procurement Portal"""
from bonfire_base import run_bonfire

def run():
    run_bonfire(
        source="fort_worth",
        agency_name="City of Fort Worth",
        portal_url="https://fortworthtexas.bonfirehub.com/portal/?tab=openOpportunities",
    )

if __name__ == "__main__":
    run()
