"""City of Plano — Bonfire/Euna Procurement Portal"""
from bonfire_base import run_bonfire

def run():
    run_bonfire(
        source="plano",
        agency_name="City of Plano",
        portal_url="https://plano.bonfirehub.com/portal/?tab=openOpportunities",
    )

if __name__ == "__main__":
    run()
