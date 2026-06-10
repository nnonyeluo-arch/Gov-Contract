"""Tarrant County — Bonfire/Euna Procurement Portal"""
from bonfire_base import run_bonfire

def run():
    run_bonfire(
        source="tarrant_county",
        agency_name="Tarrant County",
        portal_url="https://tarrantcounty.bonfirehub.com/portal/?tab=openOpportunities",
    )

if __name__ == "__main__":
    run()
