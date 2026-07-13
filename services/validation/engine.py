"""
Deterministic BMMB validation rule engine.

Packaged as a single reusable ValidationEngine so both the plain Python
path and the agentic path (agent.py's run_deterministic_checks tool) can
call it identically.

The engine only catches what a check was explicitly written to look for. It
has no visibility into pre-adapter raw extraction data, so it cannot detect
adapter/mapping bugs (e.g. a consent form's real signatory data ending up in
the wrong field) — it just sees whatever the canonical bundle says, correct
or not. That blind spot is exactly what the agentic path (agent.py) exists
to cover; see examples/test_conflict_example.py for a concrete demonstration.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .bundle import ValidationBundle
from .rules import (
    calculate_financial_18_month_rule,
    check_bank_statement_continuity,
    check_bank_statement_freshness,
    check_bank_statement_overdraft,
    check_financial_consecutive_years,
    check_ic_front_and_back,
    find_missing_ic_documents,
    fuzzy_match_entity_names,
    strict_match_entity_names,
    strict_match_ic_numbers,
    validate_form_d_expiry,
    verify_application_details_completeness,
    verify_bank_statement_duration,
    verify_consent_form_count,
    verify_consent_signatures,
    verify_financial_sections_present,
    verify_ssm_completeness,
)


class CheckResult(BaseModel):
    check: str
    passed: Optional[bool]  # None means "not applicable" — skipped, not failed
    message: str
    details: Dict[str, Any] = {}


class ValidationReport(BaseModel):
    entity_name: str
    entity_type: str
    results: List[CheckResult]

    @property
    def overall_passed(self) -> bool:
        return all(r.passed is not False for r in self.results)


class ValidationEngine:
    """Runs every applicable rules/ check against a validated bundle."""

    def run(self, bundle: ValidationBundle) -> ValidationReport:
        docs = bundle.extracted_documents
        system_date = bundle.metadata.system_date

        financial_statement_docs = [d for d in docs if d.document_type == "financial_statement"]
        tax_declaration_docs = [d for d in docs if d.document_type == "tax_declaration"]
        bank_statement_docs = [d for d in docs if d.document_type == "bank_statement"]
        identity_docs = [d for d in docs if d.document_type == "identity_document"]
        consent_form_docs = [d for d in docs if d.document_type == "consent_form"]
        ssm_form_docs = [d for d in docs if d.document_type == "ssm_corporate_form"]
        customer_info_doc = next((d for d in docs if d.document_type == "customer_information"), None)

        entity_name = ssm_form_docs[0].data.entity_name if ssm_form_docs else ""
        entity_type = ssm_form_docs[0].data.entity_type if ssm_form_docs else ""

        ssm_people_by_nric = {}
        directors_by_nric = {}
        for doc in ssm_form_docs:
            for person in doc.data.directors or []:
                directors_by_nric[person.nric_passport] = person
            for group in (doc.data.directors, doc.data.shareholders):
                for person in group or []:
                    ssm_people_by_nric[person.nric_passport] = person
        ssm_people = [p.model_dump(mode="json") for p in ssm_people_by_nric.values()]

        results: List[CheckResult] = []

        def add(check: str, result: Dict[str, Any]):
            results.append(
                CheckResult(check=check, passed=result["passed"], message=result["message"], details=result["details"])
            )

        def skip(check: str, reason: str):
            results.append(CheckResult(check=check, passed=None, message=reason, details={}))

        # --- SSM completeness ---
        if ssm_form_docs:
            add(
                "verify_ssm_completeness",
                verify_ssm_completeness(entity_type, [d.document_subtype for d in ssm_form_docs]),
            )
        else:
            skip("verify_ssm_completeness", "No ssm_corporate_form document in bundle.")

        # --- Financial statements (audited FS for Sdn Bhd, or Rule 2's
        # alternate path: 2 years of tax declarations for a Sole
        # Prop/Partnership that has no audited financial statements) ---
        if financial_statement_docs:
            latest_fye = max(d.data.financial_year_end for d in financial_statement_docs)
            add(
                "calculate_financial_18_month_rule",
                calculate_financial_18_month_rule(latest_fye.isoformat(), system_date.isoformat()),
            )
            fye_dates = [d.data.financial_year_end.isoformat() for d in financial_statement_docs]
            add("check_financial_consecutive_years", check_financial_consecutive_years(fye_dates))
            fs_data = [d.data.model_dump(mode="json") for d in financial_statement_docs]
            add("verify_financial_sections_present", verify_financial_sections_present(fs_data))
        elif tax_declaration_docs:
            latest_fye = max(d.data.financial_year_end for d in tax_declaration_docs)
            add(
                "calculate_financial_18_month_rule",
                calculate_financial_18_month_rule(latest_fye.isoformat(), system_date.isoformat()),
            )
            fye_dates = [d.data.financial_year_end.isoformat() for d in tax_declaration_docs]
            add("check_financial_consecutive_years", check_financial_consecutive_years(fye_dates))
            # Borang B is a single tax filing, not a set of financial statements --
            # there's no balance-sheet/P&L/cash-flow/auditor's-report breakdown to verify.
            skip("verify_financial_sections_present", "Tax declaration (Borang B) has no financial-section breakdown to verify.")
        else:
            for name in (
                "calculate_financial_18_month_rule",
                "check_financial_consecutive_years",
                "verify_financial_sections_present",
            ):
                skip(name, "No financial_statement or tax_declaration document in bundle.")

        # --- Bank statements ---
        if bank_statement_docs:
            statements = [
                {"start_date": d.data.statement_start_date.isoformat(), "end_date": d.data.statement_end_date.isoformat()}
                for d in bank_statement_docs
            ]
            if len(bank_statement_docs) >= 2:
                add("check_bank_statement_continuity", check_bank_statement_continuity(statements))
            else:
                skip("check_bank_statement_continuity", "Only 1 bank_statement document; continuity needs 2+.")
            add("verify_bank_statement_duration", verify_bank_statement_duration(statements, entity_type))

            latest_end_date = max(d.data.statement_end_date for d in bank_statement_docs)
            add(
                "check_bank_statement_freshness",
                check_bank_statement_freshness(latest_end_date.isoformat(), system_date.isoformat()),
            )

            monthly_balances = [
                balance.model_dump(mode="json")
                for d in bank_statement_docs
                for balance in (d.data.monthly_balances or [])
            ]
            if monthly_balances:
                add("check_bank_statement_overdraft", check_bank_statement_overdraft(monthly_balances))
            else:
                skip("check_bank_statement_overdraft", "No monthly_balances data on any bank_statement document.")
        else:
            skip("check_bank_statement_continuity", "No bank_statement document in bundle.")
            skip("verify_bank_statement_duration", "No bank_statement document in bundle.")
            skip("check_bank_statement_freshness", "No bank_statement document in bundle.")
            skip("check_bank_statement_overdraft", "No bank_statement document in bundle.")

        # --- IC documents ---
        if identity_docs:
            ic_documents = [d.data.model_dump(mode="json") for d in identity_docs]
            add("check_ic_front_and_back", check_ic_front_and_back(ic_documents))
            add("find_missing_ic_documents", find_missing_ic_documents(ssm_people, ic_documents))
        else:
            skip("check_ic_front_and_back", "No identity_document in bundle.")
            skip("find_missing_ic_documents", "No identity_document in bundle.")

        # --- Consent forms ---
        if consent_form_docs:
            consent_forms = [d.data.model_dump(mode="json") for d in consent_form_docs]
            add("verify_consent_signatures", verify_consent_signatures(ssm_people, consent_forms))
        else:
            skip("verify_consent_signatures", "No consent_form document in bundle.")

        if ssm_form_docs:
            add(
                "verify_consent_form_count",
                verify_consent_form_count(len(directors_by_nric), len(consent_form_docs)),
            )
        else:
            skip("verify_consent_form_count", "No ssm_corporate_form document in bundle -- director count unknown.")

        # --- Application details ---
        if customer_info_doc:
            add(
                "verify_application_details_completeness",
                verify_application_details_completeness(customer_info_doc.data.model_dump(mode="json")),
            )
        else:
            skip("verify_application_details_completeness", "No customer_information document in bundle.")

        # --- Form D expiry ---
        form_d_doc = next(
            (d for d in ssm_form_docs if getattr(d, "document_subtype", None) == "form_d"), None
        )
        if form_d_doc and customer_info_doc and hasattr(form_d_doc.data, "expiry_date"):
            add(
                "validate_form_d_expiry",
                validate_form_d_expiry(
                    form_d_doc.data.expiry_date.isoformat(),
                    customer_info_doc.data.tenure_months,
                    system_date.isoformat(),
                ),
            )
        else:
            skip("validate_form_d_expiry", "No ssm_corporate_form with document_subtype 'form_d' (with an expiry_date) in bundle.")

        # --- Entity name / IC number cross-matching ---
        for doc in bank_statement_docs + financial_statement_docs + tax_declaration_docs + consent_form_docs:
            target_name = doc.data.entity_name
            check_name = f"strict_match_entity_names[{doc.document_id}]"
            strict = strict_match_entity_names(entity_name, target_name)
            if strict["passed"]:
                add(check_name, strict)
            else:
                add(check_name, fuzzy_match_entity_names(entity_name, target_name))

        ic_by_nric = {d.data.nric_passport: d for d in identity_docs}
        for person in ssm_people_by_nric.values():
            ic_doc = ic_by_nric.get(person.nric_passport)
            if ic_doc is not None:
                add(
                    f"strict_match_ic_numbers[{person.name}]",
                    strict_match_ic_numbers(person.nric_passport, ic_doc.data.nric_passport),
                )

        return ValidationReport(entity_name=entity_name, entity_type=entity_type, results=results)
