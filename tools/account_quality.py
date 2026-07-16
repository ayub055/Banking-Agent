"""Account quality analysis — determines whether the bank account is the
customer's primary operating account or a salary conduit / secondary account.

Four patterns are detected deterministically from raw transaction data:
  1. Salary conduit: large outflow (≥40% of salary) within 3 days of salary credit
  2. ATM cash dependency: % of debit transactions that are ATM withdrawals
  3. Low activity: average monthly debits below threshold despite salary credits
  4. No obligation visibility: absence of EMI / utility / rent debits

All results flow into the banking LLM prompt via account_quality.observations
so the generated customer_review naturally incorporates account quality signals.
"""

import logging
from datetime import timedelta
from typing import Optional

import pandas as pd

from data.loader import get_transactions_df, load_rg_salary_data
import config.thresholds as T
from tools.category.registry import categories_with_role
from tools.rules import is_self_transfer, is_atm_debit

logger = logging.getLogger(__name__)

_SMALL_TICKET_CATS = categories_with_role("small_ticket")

# Score deltas — kept as named constants so the document is easy to update
_SCORE_EMI_PRESENT       = +15
_SCORE_UTILITY_PRESENT   = +10
_SCORE_RENT_PRESENT      = +10
_SCORE_HIGH_ACTIVITY     = +10   # avg monthly debits > 20
_SCORE_NO_CONDUIT        = +15   # zero conduit events
_SCORE_CONDUIT_MINOR     = -15   # conduit in 1-2 months
_SCORE_CONDUIT_MAJOR     = -35   # conduit in 3+ months
_SCORE_HIGH_ATM          = -20   # ATM % > 50
_SCORE_LOW_ACTIVITY      = -15   # avg monthly debits < 10
_SCORE_NO_OBLIGATIONS    = -15   # no EMI + no utility + no rent

_PRIMARY_THRESHOLD = T.AQ_PRIMARY_SCORE   # score ≥ 60 → primary account


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_conduit_events(
    debits: pd.DataFrame,
    salary_transactions: list,
    salary_amount: float,
    customer_name: Optional[str],
) -> list:
    """Return list of large-outflow events within 3 days of each salary credit.

    Only debits ≥ 40% of salary amount in the 3-day window are flagged.
    Each event dict includes whether the narration suggests a self-transfer.
    """
    events = []
    if salary_amount <= 0 or not salary_transactions:
        return events

    # Use first 6 chars of customer name for narration matching
    name_prefix = (customer_name or "").upper()[:6].strip()
    name_prefix = name_prefix if len(name_prefix) >= 3 else None

    for sal_txn in salary_transactions:
        try:
            sal_date = pd.to_datetime(sal_txn["date"])
            sal_amt  = float(sal_txn.get("amount", salary_amount) or salary_amount)
        except (ValueError, KeyError, TypeError):
            continue
        if sal_amt <= 0:
            sal_amt = salary_amount

        window_end = sal_date + timedelta(days=3)
        window = debits[
            (debits["tran_date"] >= sal_date) &
            (debits["tran_date"] <= window_end)
        ]

        for _, row in window.iterrows():
            amount = float(row["tran_amt_in_ac"])
            pct    = amount / sal_amt * 100
            if pct < T.AQ_SALARY_CONDUIT_OUTFLOW_PCT:   # below threshold — skip
                continue

            narration = str(row.get("tran_partclr", "")).upper()
            is_self = is_self_transfer(row) or bool(name_prefix and name_prefix in narration)

            events.append({
                "salary_date":       str(sal_date.date()),
                "outflow_date":      str(row["tran_date"].date()),
                "outflow_amount":    round(amount, 2),
                "pct_of_salary":     round(pct, 1),
                "days_after_salary": int((row["tran_date"] - sal_date).days),
                "is_self_transfer":  is_self,
                "narration":         str(row.get("tran_partclr", ""))[:80],
                "tran_type":         str(row.get("tran_type", "")),
            })

    return events


def _compute_atm_pct(debits: pd.DataFrame) -> float:
    """% of debit transactions identified as ATM withdrawals via the canonical
    rule (tran_type column OR ATM narration keyword)."""
    if len(debits) == 0:
        return 0.0
    is_atm = debits.apply(is_atm_debit, axis=1)
    return round(is_atm.sum() / len(debits) * 100, 1)


def _has_small_ticket(debits: pd.DataFrame) -> bool:
    """True if any debit belongs to everyday-spending categories (L2)."""
    col = "category_of_txn_l2" if "category_of_txn_l2" in debits.columns else "category_of_txn"
    if col not in debits.columns:
        return False
    cats = debits[col].dropna().str.strip()
    return bool(cats.isin(_SMALL_TICKET_CATS).any())


def _build_observations(
    account_type: str,
    conduit_events: list,
    conduit_months: int,
    salary_outflow_pct: float,
    atm_pct: float,
    avg_monthly_debits: float,
    has_emi: bool,
    has_utility: bool,
    has_rent: bool,
    salary_amount: float,
) -> list:
    """Build human-readable observation strings for the LLM prompt."""
    obs = []

    # --- Conduit signal ---
    if conduit_months >= 1:
        self_count = sum(1 for e in conduit_events if e["is_self_transfer"])
        desc = "self-transfer" if self_count >= max(1, conduit_months // 2) else "large-transfer"
        obs.append(
            f"Salary conduit detected: avg {salary_outflow_pct:.0f}% of salary transferred out "
            f"within 3 days of credit in {conduit_months} month(s) — "
            f"this appears to be a {desc} account, not the primary operating account."
        )

    # --- ATM signal ---
    if atm_pct > T.AQ_ATM_HIGH_PCT:
        obs.append(
            f"{atm_pct:.0f}% of debit transactions are ATM cash withdrawals — "
            "spending behavior is largely cash-based and invisible to banking analysis."
        )
    elif atm_pct > T.AQ_ATM_MODERATE_PCT:
        obs.append(
            f"{atm_pct:.0f}% of debit transactions are ATM withdrawals — "
            "moderate cash dependency detected."
        )

    # --- Low activity signal ---
    if avg_monthly_debits < T.AQ_LOW_ACTIVITY_DEBITS and salary_amount > 0:
        obs.append(
            f"Low account activity: avg {avg_monthly_debits:.0f} debit transactions/month "
            "despite salary credits — consistent with a non-primary account."
        )

    # --- Obligation visibility ---
    if salary_amount > 0:
        if not has_emi and not has_utility and not has_rent:
            obs.append(
                "No recurring obligations (EMI, utility bills, rent) detected in banking — "
                "loan servicing and fixed expenses may be flowing through a different account."
            )
        elif not has_emi:
            obs.append(
                "No EMI debits detected in banking — "
                "loan obligations may be serviced from another account."
            )

    # --- Positive primary signal ---
    if account_type == "primary":
        reasons = []
        if has_emi:     reasons.append("EMI payments visible")
        if has_utility: reasons.append("utility bills paid")
        if has_rent:    reasons.append("rent payments present")
        if reasons:
            obs.append(
                f"Account appears primary: {', '.join(reasons)} with "
                f"avg {avg_monthly_debits:.0f} debit transactions/month."
            )

    return obs


def _empty_result() -> dict:
    return {
        "account_type":          "unknown",
        "confidence":            "low",
        "primary_score":         50,
        "conduit_events":        [],
        "conduit_months":        0,
        "salary_outflow_pct_3d": 0.0,
        "atm_debit_pct":         0.0,
        "avg_monthly_debits":    0.0,
        "has_emi_debits":        False,
        "has_utility_debits":    False,
        "has_rent_visible":      False,
        "has_small_ticket_txns": False,
        "observations":          [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_account_quality(customer_id: int, customer_report=None,
                            rg_salary_data: Optional[dict] = None) -> dict:
    """Analyse whether this bank account is the customer's primary account.

    Args:
        customer_id:     The CRN / customer identifier.
        customer_report: The built CustomerReport (used for emis/bills/rent flags).
        rg_salary_data:  Pre-loaded salary algorithm output; loaded internally
                         when not provided (so the CSV is read once per report).

    Returns:
        dict with: account_type, confidence, primary_score, conduit_events,
                   conduit_months, salary_outflow_pct_3d, atm_debit_pct,
                   avg_monthly_debits, has_emi_debits, has_utility_debits,
                   has_rent_visible, has_small_ticket_txns, observations
    """
    # --- Load salary algorithm output (only if not passed in) ---
    if rg_salary_data is None:
        try:
            rg_salary_data = load_rg_salary_data(customer_id) or {}
        except Exception as exc:
            logger.warning("account_quality: load_rg_salary_data failed for %s: %s", customer_id, exc)
            rg_salary_data = {}

    rg_sal             = rg_salary_data.get("rg_sal") or {}
    salary_transactions = rg_sal.get("transactions", [])
    salary_amount       = float(rg_sal.get("salary_amount", 0) or 0)

    # --- Load raw transactions ---
    try:
        df      = get_transactions_df()
        cust_df = df[df["cust_id"] == customer_id].copy()
    except Exception as exc:
        logger.warning("account_quality: get_transactions_df failed for %s: %s", customer_id, exc)
        return _empty_result()

    if cust_df.empty:
        return _empty_result()

    cust_df["tran_date"] = pd.to_datetime(cust_df["tran_date"], errors="coerce")
    cust_df              = cust_df.dropna(subset=["tran_date"])
    debits               = cust_df[cust_df["dr_cr_indctor"] == "D"].copy()

    customer_name = getattr(getattr(customer_report, "meta", None), "prty_name", None)

    # -----------------------------------------------------------------------
    # Pattern 1: Salary conduit
    # -----------------------------------------------------------------------
    conduit_events = _detect_conduit_events(
        debits, salary_transactions, salary_amount, customer_name
    )
    conduit_months = len({e["salary_date"][:7] for e in conduit_events})

    # Average max outflow % per conduit month
    by_month: dict = {}
    for e in conduit_events:
        m = e["salary_date"][:7]
        if m not in by_month or e["pct_of_salary"] > by_month[m]:
            by_month[m] = e["pct_of_salary"]
    salary_outflow_pct = round(sum(by_month.values()) / len(by_month), 1) if by_month else 0.0

    # -----------------------------------------------------------------------
    # Pattern 2: ATM cash dependency
    # -----------------------------------------------------------------------
    atm_pct = _compute_atm_pct(debits)

    # -----------------------------------------------------------------------
    # Pattern 3: Account activity level
    # -----------------------------------------------------------------------
    months_count       = max(1, cust_df["tran_date"].dt.to_period("M").nunique())
    avg_monthly_debits = round(len(debits) / months_count, 1)

    # -----------------------------------------------------------------------
    # Pattern 4: Obligation visibility (from already-built report)
    # -----------------------------------------------------------------------
    has_emi     = bool(customer_report and customer_report.emis)
    has_utility = bool(customer_report and customer_report.bills)
    has_rent    = bool(customer_report and customer_report.rent)
    has_small   = _has_small_ticket(debits)

    # -----------------------------------------------------------------------
    # Primary score
    # -----------------------------------------------------------------------
    score = 50
    if has_emi:                            score += _SCORE_EMI_PRESENT
    if has_utility:                        score += _SCORE_UTILITY_PRESENT
    if has_rent:                           score += _SCORE_RENT_PRESENT
    if avg_monthly_debits > T.AQ_HIGH_ACTIVITY_DEBITS:  score += _SCORE_HIGH_ACTIVITY
    if conduit_months == 0:                score += _SCORE_NO_CONDUIT
    elif conduit_months >= T.AQ_CONDUIT_MAJOR_MONTHS:   score += _SCORE_CONDUIT_MAJOR
    else:                                  score += _SCORE_CONDUIT_MINOR
    if atm_pct > T.AQ_ATM_HIGH_PCT:        score += _SCORE_HIGH_ATM
    if avg_monthly_debits < T.AQ_LOW_ACTIVITY_DEBITS:   score += _SCORE_LOW_ACTIVITY
    if not (has_emi or has_utility or has_rent): score += _SCORE_NO_OBLIGATIONS
    score = max(0, min(100, score))

    # -----------------------------------------------------------------------
    # Classification
    # -----------------------------------------------------------------------
    if score >= _PRIMARY_THRESHOLD:
        account_type = "primary"
        confidence   = "high" if score >= T.AQ_CONFIDENCE_HIGH_SCORE else "medium"
    elif score >= T.AQ_SECONDARY_SCORE:
        account_type = "secondary"
        confidence   = "medium"
    else:
        account_type = "conduit"
        confidence   = "high" if conduit_months >= T.AQ_CONDUIT_MAJOR_MONTHS else "medium"

    # No salary data at all → unknown
    if not salary_transactions and salary_amount == 0:
        if not (customer_report and customer_report.salary):
            account_type = "unknown"
            confidence   = "low"

    # -----------------------------------------------------------------------
    # Human-readable observations for LLM prompt
    # -----------------------------------------------------------------------
    observations = _build_observations(
        account_type, conduit_events, conduit_months, salary_outflow_pct,
        atm_pct, avg_monthly_debits, has_emi, has_utility, has_rent, salary_amount,
    )

    return {
        "account_type":          account_type,
        "confidence":            confidence,
        "primary_score":         score,
        "conduit_events":        conduit_events,
        "conduit_months":        conduit_months,
        "salary_outflow_pct_3d": salary_outflow_pct,
        "atm_debit_pct":         atm_pct,
        "avg_monthly_debits":    avg_monthly_debits,
        "has_emi_debits":        has_emi,
        "has_utility_debits":    has_utility,
        "has_rent_visible":      has_rent,
        "has_small_ticket_txns": has_small,
        "observations":          observations,
    }
