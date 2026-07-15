"""Pydantic request/response models for the aggregation service.

The bank input mirrors what services.extraction returns for a bank-statement
template (daily transaction rows); the output is the aggregated shape the
frontend and validation consume. Keeping these as explicit models means the
API contract is self-documenting and validated at the boundary.
"""
from typing import Optional

from pydantic import BaseModel, Field


# ---- input: raw extraction (one per bank-statement document) --------------

class BankTransaction(BaseModel):
    date: Optional[str] = None          # normalised YYYY-MM-DD
    description: Optional[str] = None
    debit: Optional[float] = None       # money out, positive magnitude
    credit: Optional[float] = None      # money in, positive magnitude
    balance: Optional[float] = None     # running balance shown after the row
    real_page: Optional[int] = None


class BankStatementExtraction(BaseModel):
    source_document: Optional[str] = None
    bank_name: Optional[str] = None
    account_number_masked: Optional[str] = None
    statement_period: Optional[str] = None
    transactions: list[BankTransaction] = Field(default_factory=list)


class BankAggregateRequest(BaseModel):
    documents: list[BankStatementExtraction]


# ---- output: aggregated per (bank, account) -------------------------------

class BankMonthSummary(BaseModel):
    month: str                          # YYYY-MM
    txn_count: int
    monthly_deposit: float
    monthly_withdrawal: float
    monthly_end_balance: Optional[float] = None


class BankYearSummary(BaseModel):
    year: int
    months_covered: int
    avg_monthly_deposit: float
    avg_monthly_withdrawal: float
    avg_monthly_end_balance: Optional[float] = None


class BankAccountSummary(BaseModel):
    bank_name: Optional[str] = None
    account_number_masked: Optional[str] = None
    source_documents: list[str] = Field(default_factory=list)
    monthly: list[BankMonthSummary] = Field(default_factory=list)
    yearly: list[BankYearSummary] = Field(default_factory=list)
    integrity_warnings: list[str] = Field(default_factory=list)


class BankAggregateResponse(BaseModel):
    accounts: list[BankAccountSummary]
