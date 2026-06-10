"""Bexar County — Bonfire/Euna Procurement Portal"""
from bonfire_base import run_bonfire

def run():
    run_bonfire(
        source="bexar_county",
        agency_name="Bexar County",
        portal_url="https://bexarcounty.bonfirehub.com/portal/?tab=openOpportunities",
    )

if __name__ == "__main__":
    run()
