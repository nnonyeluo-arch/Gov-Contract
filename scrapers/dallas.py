"""City of Dallas — Bonfire/Euna Procurement Portal"""
from bonfire_base import run_bonfire

def run():
    run_bonfire(
        source="dallas",
        agency_name="City of Dallas",
        portal_url="https://dallas.bonfirehub.com/portal/?tab=openOpportunities",
    )

if __name__ == "__main__":
    run()
