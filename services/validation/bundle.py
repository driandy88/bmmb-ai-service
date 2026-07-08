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
    balance_sheet_present: bool
    profit_and_loss_present: bool
    cash_flow_present: bool
    auditors_report_present: bool

class BankStatementData(BaseModel):
    entity_name: str
    statement_start_date: date
    statement_end_date: date

class IdentityDocumentData(BaseModel):
    individual_name: str
    nric_passport: str
    front_image_present: bool
    back_image_present: bool
    expiry_date: Optional[date] = None

class ConsentFormData(BaseModel):
    entity_name: str
    individual_name: str
    nric_passport: str
    position: Optional[str] = None
    signature_present: bool

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
    document_subtype: Optional[Literal["form_24", "form_44", "form_49", "form_b", "form_d"]] = None
    data: SsmCorporateFormData

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