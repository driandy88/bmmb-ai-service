"""Unit tests for the deterministic bank aggregation. No network, no LLM."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from services.aggregation.api import app
from services.aggregation.bank import aggregate_bank

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _one_account(result):
    assert len(result["accounts"]) == 1
    return result["accounts"][0]


def test_single_statement_daily_to_monthly_to_yearly():
    docs = [{
        "source_document": "s.pdf",
        "bank_name": "SCB",
        "account_number_masked": "****4321",
        "transactions": [
            {"date": "2026-01-10", "credit": 12000, "balance": 12000},
            {"date": "2026-01-20", "debit": 3500, "balance": 8500},
            {"date": "2026-01-28", "debit": 2000, "balance": 6500},
            {"date": "2026-02-05", "credit": 9000, "balance": 15500},
            {"date": "2026-02-25", "debit": 6000, "balance": 9500},
        ],
    }]
    acc = _one_account(aggregate_bank(docs))
    assert acc["integrity_warnings"] == []

    jan, feb = acc["monthly"]
    assert (jan["month"], jan["monthly_deposit"], jan["monthly_withdrawal"], jan["monthly_end_balance"]) \
        == ("2026-01", 12000, 5500, 6500)
    assert (feb["month"], feb["monthly_deposit"], feb["monthly_withdrawal"], feb["monthly_end_balance"]) \
        == ("2026-02", 9000, 6000, 9500)

    (year,) = acc["yearly"]
    assert year["year"] == 2026 and year["months_covered"] == 2
    assert year["avg_monthly_deposit"] == 10500
    assert year["avg_monthly_withdrawal"] == 5750
    assert year["avg_monthly_end_balance"] == 8000


def test_multiple_statements_pool_into_one_account():
    docs = [
        {"source_document": "jan.pdf", "bank_name": "SCB", "account_number_masked": "****4321",
         "transactions": [
             {"date": "2026-01-10", "credit": 12000, "balance": 12000},
             {"date": "2026-01-20", "debit": 5500, "balance": 6500},
         ]},
        {"source_document": "feb.pdf", "bank_name": "SCB", "account_number_masked": "****4321",
         "transactions": [
             {"date": "2026-02-05", "credit": 9000, "balance": 15500},
             {"date": "2026-02-25", "debit": 6000, "balance": 9500},
         ]},
    ]
    acc = _one_account(aggregate_bank(docs))
    assert acc["source_documents"] == ["jan.pdf", "feb.pdf"]
    assert len(acc["monthly"]) == 2
    assert acc["yearly"][0]["months_covered"] == 2
    assert acc["yearly"][0]["avg_monthly_end_balance"] == 8000


def test_balance_continuity_warning_flags_a_bad_row():
    docs = [{
        "source_document": "s.pdf", "bank_name": "SCB", "account_number_masked": "****4321",
        "transactions": [
            {"date": "2026-01-10", "credit": 1000, "balance": 1000},
            {"date": "2026-01-20", "debit": 300, "balance": 999},   # expected 700
        ],
    }]
    acc = _one_account(aggregate_bank(docs))
    assert len(acc["integrity_warnings"]) == 1
    w = acc["integrity_warnings"][0]
    assert "999" in w and "expected 700" in w and "s.pdf" in w


def test_separate_banks_yield_separate_accounts_sorted():
    docs = [
        {"source_document": "b.pdf", "bank_name": "RHB", "account_number_masked": "**9",
         "transactions": [{"date": "2026-03-01", "credit": 100, "balance": 100}]},
        {"source_document": "a.pdf", "bank_name": "CIMB", "account_number_masked": "**1",
         "transactions": [{"date": "2026-03-01", "credit": 200, "balance": 200}]},
    ]
    result = aggregate_bank(docs)
    assert [a["bank_name"] for a in result["accounts"]] == ["CIMB", "RHB"]  # order-stable


def test_tolerates_non_iso_dates_and_warns_on_unparseable():
    # extraction should emit ISO, but the aggregator must not crash on a stray format
    docs = [{
        "source_document": "s.pdf", "bank_name": "SCB", "account_number_masked": "**1",
        "transactions": [
            {"date": "23 Jan 2026", "credit": 1000, "balance": 1000},   # 'DD Mon YYYY'
            {"date": "05/02/2026", "debit": 400, "balance": 600},       # 'DD/MM/YYYY'
            {"date": "not a date", "debit": 50, "balance": 550},        # unparseable -> warned + skipped
        ],
    }]
    acc = _one_account(aggregate_bank(docs))
    assert [m["month"] for m in acc["monthly"]] == ["2026-01", "2026-02"]
    assert acc["monthly"][0]["monthly_deposit"] == 1000
    assert acc["monthly"][1]["monthly_withdrawal"] == 400   # the 'not a date' debit is excluded
    assert any("unparseable" in w for w in acc["integrity_warnings"])


def test_empty_inputs_do_not_crash():
    assert aggregate_bank([]) == {"accounts": []}
    # a statement with no transactions still yields an account, with empty rollups
    acc = _one_account(aggregate_bank([
        {"source_document": "x.pdf", "bank_name": "SCB", "account_number_masked": "**1", "transactions": []}
    ]))
    assert acc["monthly"] == [] and acc["yearly"] == [] and acc["integrity_warnings"] == []


def test_shipped_example_is_valid_input():
    with open(EXAMPLES / "bank_extraction_example.json") as f:
        payload = json.load(f)
    acc = _one_account(aggregate_bank(payload["documents"]))
    assert acc["yearly"][0]["avg_monthly_end_balance"] == 8000


def test_api_health_and_aggregate():
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}

    with open(EXAMPLES / "bank_extraction_example.json") as f:
        payload = json.load(f)
    resp = client.post("/aggregate/bank", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["accounts"]) == 1
    assert body["accounts"][0]["yearly"][0]["avg_monthly_deposit"] == 10500
