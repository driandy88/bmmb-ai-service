"""
Document → Tier-1 slot mapping (agents/eligibility/document_map.py).

Covers: scalar fields, most-recent selection from multi-period arrays, array
located by CONTENT (not key name), date→years, equity fallback, and the Tier-1
boundary (Tier-2 fields in the same document are never mapped).
"""
from datetime import date

from app.agents.eligibility import document_map as dm

TODAY = date(2026, 7, 28)


def test_ssm_incorporation_date_to_age():
    got = dm.map_document_to_slots(
        "business_registration_ssm",
        {"business_name": "Prisma Niaga Sdn Bhd", "incorporation_date": "15 Mac / March 2018"},
        today=TODAY,
    )
    assert got == {"business_age_years": 8.0}   # 2026 - 2018


def test_customer_info_financing_request():
    got = dm.map_document_to_slots(
        "customer_information_details",
        {"names": ["A"], "financing_request_volume": 300000.0},
        today=TODAY,
    )
    assert got == {"working_capital_limit": 300000.0}


def test_afs_takes_most_recent_year_and_ignores_tier2():
    afs = {"audited_financial_statements": [
        {"financial_year": "FY2022", "revenue_turnover_gross_profit": 3_910_000.0,
         "total_equity": 812_300.0, "ebitda_net_profit": 610_000.0, "pbt": 500_000.0},
        {"financial_year": "FY2023", "revenue_turnover_gross_profit": 4_820_500.0,
         "total_equity": 1_060_425.0, "ebitda_net_profit": 803_800.0, "pbt": 606_700.0},
    ]}
    got = dm.map_document_to_slots("audited_financial_statements", afs, today=TODAY)
    assert got == {"revenue": 4_820_500.0, "total_equity_or_net_worth": 1_060_425.0}  # FY2023
    # Tier-2 fields must never leak into the slots.
    assert "ebitda_net_profit" not in got and "pbt" not in got


def test_equity_falls_back_to_net_worth():
    afs = {"rows": [{"financial_year": "FY2023", "revenue_turnover_gross_profit": 2_000_000.0,
                     "total_equity": None, "net_worth": 950_000.0}]}
    got = dm.map_document_to_slots("audited_financial_statements", afs, today=TODAY)
    assert got["total_equity_or_net_worth"] == 950_000.0


def test_group_array_located_by_content_not_key_name():
    # Array under an arbitrary key the mapper cannot know a priori.
    bank = {"whatever_the_db_calls_it": [
        {"month": "June 2023", "monthly_end_balance": 210_400.0},
        {"month": "July 2023", "monthly_end_balance": 266_750.0},
    ]}
    got = dm.map_document_to_slots("bank_statements", bank, today=TODAY)
    assert got == {"end_balance": 266_750.0}   # most recent month


def test_missing_fields_yield_no_slots():
    assert dm.map_document_to_slots("bank_statements", {"bank_statements": []}, today=TODAY) == {}
    assert dm.map_document_to_slots("business_registration_ssm", {}, today=TODAY) == {}
