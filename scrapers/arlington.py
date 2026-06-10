"""City of Arlington — Bonfire/Euna Procurement Portal"""
from bonfire_base import run_bonfire

def run():
    run_bonfire(
        source="arlington",
        agency_name="City of Arlington",
        portal_url="https://arlington.bonfirehub.com/portal/?tab=openOpportunities",
    )

if __name__ == "__main__":
    run()
