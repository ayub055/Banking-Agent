"""Canonical row-level business-rule predicates.

Single home for the questions "is this transaction a salary credit / EMI
debit / loan disbursal?" and "is this an inherently recurring category?".
Every predicate is built on the category registry (role tags) and the
centralised keyword sets in ``config/keywords`` — never on inline string
literals or per-caller thresholds.

Callers (analytics, report builders, event detection, renderers) should use
these helpers so the same customer is classified identically everywhere.
"""

from __future__ import annotations

from typing import Optional

from tools.category.registry import has_role, l2_canonical
from utils.narration_utils import is_salary_narration


def is_salary_credit(l2: Optional[str], narration: Optional[str]) -> bool:
    """Salary rule: registry salary role OR a salary narration keyword.

    Mirrors the dual check used by the deterministic salary block so income
    analytics and the report agree on what counts as a salary credit.
    """
    return has_role(l2, "salary") or is_salary_narration(str(narration or ""))


def is_emi_debit(l2: Optional[str], narration: Optional[str]) -> bool:
    """EMI rule: registry EMI role OR an EMI narration keyword."""
    if has_role(l2, "emi"):
        return True
    from config.keywords import EMI_ALL_KEYWORDS
    up = str(narration or "").upper()
    return any(kw.upper() in up for kw in EMI_ALL_KEYWORDS)


def is_loan_disbursal(l2: Optional[str], narration: Optional[str]) -> bool:
    """Loan-disbursal rule: L2 ``Loan_Disburse`` OR a disbursal/lender keyword."""
    if l2_canonical(l2) == "Loan_Disburse":
        return True
    from config.keywords import LOAN_DISBURSEMENT_KEYWORDS, LENDER_FRAGMENTS
    up = str(narration or "").upper()
    return (any(kw in up for kw in LOAN_DISBURSEMENT_KEYWORDS)
            or any(f in up for f in LENDER_FRAGMENTS))


def is_self_transfer(row) -> bool:
    """Canonical self-transfer rule (union): the ``self_transfer`` column flag
    OR the registry ``self_transfer`` role OR a self-transfer narration keyword.

    ``row`` is any mapping with ``.get`` (a dict or a pandas Series). Callers
    that also match the customer's own name should OR that in separately.
    """
    if row.get("self_transfer") in (1, "1", True) or str(row.get("self_transfer", "")).strip() == "1":
        return True
    if has_role(row.get("category_of_txn_l2"), "self_transfer"):
        return True
    from config.keywords import SELF_TRANSFER_KEYWORDS
    up = str(row.get("tran_partclr", "") or "").upper()
    return any(kw in up for kw in SELF_TRANSFER_KEYWORDS)


def is_atm_debit(row) -> bool:
    """Canonical ATM-withdrawal rule (union): the ``tran_type`` column contains
    ``ATM`` OR the narration matches an ATM withdrawal keyword.

    ``row`` is any mapping with ``.get`` (a dict or a pandas Series).
    """
    if "ATM" in str(row.get("tran_type", "") or "").upper():
        return True
    import re
    from config.keywords import ATM_WITHDRAWAL_KEYWORDS
    from utils.narration_utils import like_to_regex
    up = str(row.get("tran_partclr", "") or "").upper()
    return any(re.search(like_to_regex(kw), up) for kw in ATM_WITHDRAWAL_KEYWORDS)


def is_recurring_category(l2: Optional[str]) -> bool:
    """'Is this an inherently recurring category?' — registry recurring role.

    Note: this is the *category-property* sense of recurring (Salary, EMI,
    Rent, …). It is deliberately distinct from event-detection's "observed in
    >= N months" sense, which lives in ``tools/event_detector``.
    """
    return has_role(l2, "recurring")
