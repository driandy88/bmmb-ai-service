"""
The canonical ValidationBundle schema -- what ValidationEngine (engine.py)
runs against, and what extraction_adapter.py's build_validation_bundle()
produces from raw extraction results.
"""

from datetime import date
from typing import Annotated, List, Literal, Optional, Union
from pydantic import BaseModel, Field

# ---------------------------------------------------------
# 1. Shared Data Models
# ---------------------------------------------------------
class PersonInfo(BaseModel):
    name: str
    nric_passport: str
    position: Optional[str] = None

# ---------------------------------------------------------
# 2. Document Data Payloads
# ---------------------------------------------------------
class CustomerInfoData(BaseModel):
    main_contact_names: List[str]
    main_contact_emails: List[str]
    main_contact_phone_numbers: List[str]
    financing_amount: float
    product_type: str
    tenure_months: int
    repayment_frequency: str

class SsmCorporateFormData(BaseModel):
    entity_name: str
    business_registration_number: str
    entity_type: str
    directors: Optional[List[PersonInfo]] = None
    shareholders: Optional[List[PersonInfo]] = None

class FinancialStatementData(BaseModel):
    entity_name: str
    financial_year_end: date
    # Optional, not bool: None means "extraction couldn't determine this,"
    # which is a different thing from False ("confirmed absent") -- the
    # engine (verify_financial_sections_present) treats them differently:
    # False fails the check, None marks it needs-review instead.
    balance_sheet_present: Optional[bool] = None
    profit_and_loss_present: Optional[bool] = None
    cash_flow_present: Optional[bool] = None
    auditors_report_present: Optional[bool] = None

# Rule 2's alternate path to audited financial statements: a Sole
# Prop/Partnership may submit 2 years of LHDN tax declarations (Borang B)
# instead. Same 2-consecutive-year / 18-month-freshness checks apply, but
# there's no balance-sheet/P&L/cash-flow/auditor's-report breakdown to
# verify -- Borang B is a single tax filing, not a set of financial
# statements.
class TaxDeclarationData(BaseModel):
    entity_name: str
    financial_year_end: date

class MonthlyBankBalance(BaseModel):
    month: str  # e.g. "July 2023" -- matches extraction's "Bank Statement Month" attribute verbatim
    end_balance: float

class BankStatementData(BaseModel):
    entity_name: str
    statement_start_date: date
    statement_end_date: date
    # One entry per month covered by this statement -- used for the
    # overdraft check (Rule 3c): a negative end balance on a debit
    # (current/savings) account indicates an overdraft.
    monthly_balances: Optional[List[MonthlyBankBalance]] = None

class IdentityDocumentData(BaseModel):
    individual_name: str
    nric_passport: str
    # Optional, not bool -- see FinancialStatementData's note. None means
    # "couldn't tell from the image," not "confirmed missing."
    front_image_present: Optional[bool] = None
    back_image_present: Optional[bool] = None
    expiry_date: Optional[date] = None

class ConsentFormData(BaseModel):
    entity_name: str
    individual_name: str
    nric_passport: str
    position: Optional[str] = None
    # Optional, not bool -- see FinancialStatementData's note. None means
    # "not confirmed" (e.g. no signature-detection signal available), not
    # "confirmed unsigned."
    signature_present: Optional[bool] = None

# ---------------------------------------------------------
# 3. Document Wrappers (Using Literals for Discrimination)
# ---------------------------------------------------------
class CustomerInfoDoc(BaseModel):
    document_id: str
    document_type: Literal["customer_information"]
    data: CustomerInfoData

class SsmCorporateDoc(BaseModel):
    document_id: str
    document_type: Literal["ssm_corporate_form"]
    # form_24/44/49 (Sdn Bhd) or form_b/form_d (Sole Prop/Partnership) are the
    # required set (Rule 1); form_9/form_15/form_58/annual_return are
    # optional extras a Sdn Bhd may additionally submit -- accepted here but
    # never required by verify_ssm_completeness.
    document_subtype: Optional[Literal[
        "form_24", "form_44", "form_49", "form_b", "form_d",
        "form_9", "form_15", "form_58", "annual_return",
    ]] = None
    data: SsmCorporateFormData

class TaxDeclarationDoc(BaseModel):
    document_id: str
    document_type: Literal["tax_declaration"]
    data: TaxDeclarationData

class FinancialStatementDoc(BaseModel):
    document_id: str
    document_type: Literal["financial_statement"]
    data: FinancialStatementData

class BankStatementDoc(BaseModel):
    document_id: str
    document_type: Literal["bank_statement"]
    data: BankStatementData

class IdentityDoc(BaseModel):
    document_id: str
    document_type: Literal["identity_document"]
    data: IdentityDocumentData

class ConsentFormDoc(BaseModel):
    document_id: str
    document_type: Literal["consent_form"]
    data: ConsentFormData

# Combine all document types into a discriminated Union
DocumentTypeUnion = Annotated[
    Union[
        CustomerInfoDoc,
        SsmCorporateDoc,
        TaxDeclarationDoc,
        FinancialStatementDoc,
        BankStatementDoc,
        IdentityDoc,
        ConsentFormDoc
    ],
    Field(discriminator="document_type"),
]

# ---------------------------------------------------------
# 4. Root Bundle Model
# ---------------------------------------------------------
class BundleMetadata(BaseModel):
    total_documents_received: int
    system_date: date
    document_types_present: List[str]

class ValidationBundle(BaseModel):
    bundle_id: str
    metadata: BundleMetadata
    extracted_documents: List[DocumentTypeUnion]
