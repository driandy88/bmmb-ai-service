"""Terminology lint + PII redaction (brief §10 Part A, §2, §12)."""
from services.chat.app.utils import pii, terminology


def test_loan_rewritten_to_financing():
    r = terminology.lint("You can get a loan for your business.")
    assert "loan" not in r.text.lower() and "financing" in r.text.lower()
    assert r.violations == ["loan"]


def test_interest_rate_rewritten_to_profit_rate():
    r = terminology.lint("Our interest rate is competitive.")
    assert "interest" not in r.text.lower() and "profit rate" in r.text.lower()


def test_case_preserved():
    assert terminology.lint("Loan approved").text.startswith("Financing")
    assert "FINANCING" in terminology.lint("LOAN terms").text


def test_clean_text_untouched():
    ok = "Here is your SME financing summary with the profit rate applied."
    r = terminology.lint(ok)
    assert r.clean and r.text == ok


def test_deliberate_contrast_loan_is_kept_not_self_contradicted():
    # The Islamic-framing case: the bot deliberately contrasts our financing with a conventional
    # loan. The lint must NOT rewrite the contrast into "financing rather than conventional
    # financing" (the exact awkwardness the stress test caught).
    quoted = terminology.lint("We offer 'financing' rather than a conventional 'loan'.")
    assert "'loan'" in quoted.text and quoted.clean          # quoted term kept, not a violation
    qualified = terminology.lint("This is financing, not a conventional loan.")
    assert "conventional loan" in qualified.text and qualified.clean


def test_bare_loan_still_rewritten_after_the_exemption():
    # Compliance backstop intact: a bare "loan" about our own product is still corrected.
    r = terminology.lint("Apply for our business loan today.")
    assert "loan" not in r.text.lower() and "financing" in r.text.lower()
    assert r.violations == ["loan"]


def test_pii_redacts_ic_email_phone_money():
    raw = "IC 900101-01-1234, email a.b@muamalat.com.my, call 017-621 5751, revenue RM 1,000,000"
    out = pii.redact(raw)
    assert "900101-01-1234" not in out and "[IC_REDACTED]" in out
    assert "@muamalat" not in out and "[EMAIL_REDACTED]" in out
    assert "017-621" not in out and "[PHONE_REDACTED]" in out
    assert "1,000,000" not in out and "[AMOUNT_REDACTED]" in out


def test_redact_obj_is_recursive():
    obj = {"note": "call 017-621 5751", "nested": ["RM 500,000"]}
    red = pii.redact_obj(obj)
    assert "[PHONE_REDACTED]" in red["note"] and "[AMOUNT_REDACTED]" in red["nested"][0]
