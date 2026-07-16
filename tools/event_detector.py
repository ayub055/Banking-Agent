"""Transaction event detection engine.

Discovers meaningful semantic events from raw narrations and transaction patterns.
Converts 500-2000 raw rows into 5-15 structured events ready for LLM narration.

Architecture:
  Two detection layers:
    1. KEYWORD_RULES  — simple narration keyword matching (PF, FD, SIP, BNPL, etc.)
    2. Custom detectors — multi-step logic (post-salary routing, loan redistribution, round-trips)

Adding a new pattern requires only:
  - Adding an entry to KEYWORD_RULES (keyword match), OR
  - Writing a _detect_<pattern>() function and calling it in detect_events()
  No changes needed to intents, templates, prompts, or renderers.
"""

import logging
import re
from datetime import timedelta
from typing import Optional

import pandas as pd

from data.loader import get_transactions_df, load_rg_salary_data
from utils.narration_utils import extract_recipient_name
from config.keywords import (
    LENDER_FRAGMENTS,
    LOAN_DISBURSEMENT_KEYWORDS,
    SALARY_CREDIT_FRAGMENTS,
    EVENT_KEYWORD_RULES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Priority order: high → medium → positive
_SIG_ORDER = {"high": 0, "medium": 1, "positive": 2}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from utils.narration_utils import like_to_regex as _kw_to_regex

def _narr_upper(row) -> str:
    return str(row.get("tran_partclr", "")).upper()


def _short_source(narration: str) -> str:
    """Extract a short source label from a narration string."""
    narration = narration.upper()
    for lender in LENDER_FRAGMENTS:
        if lender in narration:
            return lender.strip().title()
    for kw in LOAN_DISBURSEMENT_KEYWORDS:
        if kw in narration:
            return "Bank/NBFC"
    return "Unknown source"


def _extract_name_from_narration(narration: str) -> Optional[str]:
    """Extract recipient/remitter name from narration.

    Delegates to extract_recipient_name which handles IFT, UPI, IMPS,
    RTGS, MB:RECEIVED, and NEFT patterns. Falls back to NEFT slash
    pattern for edge cases like "NEFT/IFSC/NAME/BANK".
    """
    name = extract_recipient_name(narration)
    if name:
        return name.title()

    upper = narration.upper()

    # Fallback: NEFT slash format "NEFT/IFSC/NAME/BANK"
    neft_m = re.search(r"NEFT/[A-Z0-9]+/([A-Z\s]+)/", upper)
    if neft_m:
        candidate = neft_m.group(1).strip()
        if 3 <= len(candidate) <= 40:
            return candidate.title()

    return None


def _is_self(row, name_prefix: Optional[str]) -> bool:
    """Self-transfer check: canonical union rule (flag/role/keyword) OR a match
    against the customer's own name prefix."""
    from tools.rules import is_self_transfer
    if is_self_transfer(row):
        return True
    upper = _narr_upper(row)
    return bool(name_prefix and len(name_prefix) >= 3 and name_prefix in upper)


def _month_label(dt) -> str:
    return pd.Timestamp(dt).strftime("%b %Y")


def _compute_monthly_credit_stats(df: pd.DataFrame):
    """Compute monthly credit totals and median from a customer's transaction df.

    Returns (credits_df, monthly_totals Series, median_monthly_credit)
    or (None, None, None) if insufficient data (< 3 months or median < 1000).
    """
    credits = df[df["dr_cr_indctor"] == "C"].copy()
    if credits.empty:
        return None, None, None
    credits["_month"] = credits["tran_date"].dt.to_period("M")
    monthly_totals = credits.groupby("_month")["tran_amt_in_ac"].sum()
    if len(monthly_totals) < 3:
        return None, None, None
    median_credit = monthly_totals.median()
    if median_credit < 1000:
        return None, None, None
    return credits, monthly_totals, median_credit


def _classify_credit_source(narration: str) -> str:
    """Classify a credit narration into a human-readable source label."""
    upper = narration.upper()
    # Loan disbursal
    if any(kw in upper for kw in LOAN_DISBURSEMENT_KEYWORDS):
        return "possible loan disbursal"
    if any(lender in upper for lender in LENDER_FRAGMENTS):
        return "credit from bank/NBFC"
    # Salary
    for kw in SALARY_CREDIT_FRAGMENTS:
        if kw in upper:
            return "salary/employer credit"
    # Government
    for kw in ("DBT", "GOVT", "PENSION", "PM KISAN", "MNREGA", "SCHOLARSHIP", "TAX REFUND"):
        if kw in upper:
            return "government credit"
    # Individual transfer
    if any(ch in upper for ch in ("UPI/", "IMPS/", "NEFT/", "RTGS/")):
        name = _extract_name_from_narration(narration)
        if name:
            return f"transfer from {name}"
        return "individual transfer"
    return narration[:50]


# ---------------------------------------------------------------------------
# Layer 1: Keyword rule matching
# ---------------------------------------------------------------------------

def _apply_keyword_rules(df: pd.DataFrame) -> list:
    """Apply all KEYWORD_RULES to the transaction DataFrame."""
    events = []

    for rule in EVENT_KEYWORD_RULES:
        direction = rule["direction"]
        keywords  = rule["keywords"]
        min_months = rule.get("min_months", 0)

        # Filter by direction
        if direction == "C":
            subset = df[df["dr_cr_indctor"] == "C"]
        elif direction == "D":
            subset = df[df["dr_cr_indctor"] == "D"]
        else:
            subset = df

        if subset.empty:
            continue

        # Match rows — keywords support SQL-style % wildcard (matches any chars)
        narrations = subset["tran_partclr"].fillna("").str.upper()
        pattern    = "|".join(_kw_to_regex(kw) for kw in keywords)
        matched    = subset[narrations.str.contains(pattern, na=False, regex=True)].copy()

        if matched.empty:
            continue

        matched["tran_date"] = pd.to_datetime(matched["tran_date"], errors="coerce")
        matched = matched.dropna(subset=["tran_date"])

        if matched.empty:
            continue

        # Group by calendar month
        matched["_month"] = matched["tran_date"].dt.to_period("M")
        month_groups = matched.groupby("_month")
        n_months = len(month_groups)

        if min_months > 0 and n_months < min_months:
            continue  # Recurring check — skip if too few months

        if min_months > 0:
            # Recurring pattern — summarise across months
            avg_amt = matched["tran_amt_in_ac"].mean()
            first_m = matched["tran_date"].min()
            sample_narr = str(matched.iloc[0].get("tran_partclr", ""))[:60]
            events.append({
                "type":        rule["type"],
                "date":        str(first_m.date()),
                "month_label": f"Ongoing ({n_months} months)",
                "amount":      round(avg_amt, 2),
                "significance": rule["significance"],
                "description": (
                    f"Ongoing ({n_months} months): {rule['label']} — "
                    f"avg ₹{avg_amt:,.0f}/month ({sample_narr})"
                ),
            })
        else:
            # Per-occurrence events
            for _, row in matched.iterrows():
                amt = float(row["tran_amt_in_ac"])
                narr = str(row.get("tran_partclr", ""))
                events.append({
                    "type":        rule["type"],
                    "date":        str(row["tran_date"].date()),
                    "month_label": _month_label(row["tran_date"]),
                    "amount":      round(amt, 2),
                    "significance": rule["significance"],
                    "description": (
                        f"{_month_label(row['tran_date'])}: {rule['label']} — "
                        f"₹{amt:,.0f} ({narr[:70]})"
                    ),
                })

    return events


# ---------------------------------------------------------------------------
# Layer 2: Custom multi-step detectors
# ---------------------------------------------------------------------------

def _detect_post_salary_routing(
    df: pd.DataFrame,
    salary_txns: list,
    salary_amount: float,
    customer_name: Optional[str],
) -> list:
    """Detect multi-recipient fund routing within 48h of each salary credit.

    Different from account_quality conduit (which looks for self-transfer of
    a single large amount). This detects distribution to 2+ DIFFERENT people.
    """
    events = []
    if not salary_txns or salary_amount <= 0:
        return events

    debits     = df[df["dr_cr_indctor"] == "D"].copy()
    name_pfx   = (customer_name or "").upper()[:6].strip() or None

    for sal_txn in salary_txns:
        try:
            sal_date = pd.to_datetime(sal_txn["date"])
            sal_amt  = float(sal_txn.get("amount", salary_amount) or salary_amount)
        except (ValueError, KeyError, TypeError):
            continue
        if sal_amt <= 0:
            sal_amt = salary_amount

        window = debits[
            (debits["tran_date"] >= sal_date) &
            (debits["tran_date"] <= sal_date + timedelta(hours=48)) &
            (debits["tran_amt_in_ac"] >= sal_amt * 0.08)   # ≥8% each (not micro transactions)
        ]

        if len(window) < 2:
            continue

        recipients = []
        for _, row in window.iterrows():
            narr   = _narr_upper(row)
            amt    = float(row["tran_amt_in_ac"])
            name   = _extract_name_from_narration(str(row.get("tran_partclr", "")))
            is_own = _is_self(row, name_pfx)
            recipients.append({
                "name":    name or ("SELF" if is_own else "Unknown"),
                "amount":  amt,
                "is_self": is_own,
                "narr":    str(row.get("tran_partclr", ""))[:50],
            })

        # Require 2+ distinct recipients (or 1 non-self + total >50%)
        distinct = len({r["name"] for r in recipients if not r["is_self"] and r["name"] != "Unknown"})
        total_routed = sum(r["amount"] for r in recipients)
        pct_routed   = total_routed / sal_amt * 100

        if distinct < 2 and not (distinct >= 1 and pct_routed >= 50):
            continue

        recip_str = ", ".join(
            f"{r['name']} (₹{r['amount']:,.0f})"
            for r in recipients[:4]
        )
        events.append({
            "type":        "post_salary_routing",
            "date":        str(sal_date.date()),
            "month_label": _month_label(sal_date),
            "amount":      round(total_routed, 2),
            "significance": "high",
            "description": (
                f"{_month_label(sal_date)}: Post-salary routing — "
                f"₹{total_routed:,.0f} ({pct_routed:.0f}% of ₹{sal_amt:,.0f} salary) "
                f"distributed to {len(recipients)} recipient(s) within 48h: {recip_str}"
            ),
        })

    return events


def _detect_loan_redistribution(df: pd.DataFrame, salary_amount: float) -> list:
    """Detect large credits (possible loan disbursal) followed by multi-person distribution."""
    events = []

    threshold = max(150000, salary_amount * 2.0) if salary_amount > 0 else 150000
    credits   = df[df["dr_cr_indctor"] == "C"].copy()
    debits    = df[df["dr_cr_indctor"] == "D"].copy()

    from tools.rules import is_loan_disbursal

    for _, row in credits[credits["tran_amt_in_ac"] >= threshold].iterrows():
        narr   = _narr_upper(row)
        amount = float(row["tran_amt_in_ac"])

        # Canonical disbursal narration rule (l2=None keeps this a pure
        # keyword/lender check, matching the historical detector semantics).
        if not is_loan_disbursal(None, narr):
            continue

        txn_date   = row["tran_date"]
        window_end = txn_date + timedelta(hours=48)
        outflows   = debits[
            (debits["tran_date"] >= txn_date) &
            (debits["tran_date"] <= window_end) &
            (debits["tran_amt_in_ac"] >= 5000)
        ]

        if len(outflows) < 2:
            continue

        total_out = float(outflows["tran_amt_in_ac"].sum())
        pct_out   = total_out / amount * 100
        if pct_out < 30:
            continue

        out_str = ", ".join(
            f"₹{float(r['tran_amt_in_ac']):,.0f}"
            for _, r in outflows.head(4).iterrows()
        )
        events.append({
            "type":        "loan_redistribution_suspect",
            "date":        str(txn_date.date()),
            "month_label": _month_label(txn_date),
            "amount":      amount,
            "significance": "high",
            "description": (
                f"{_month_label(txn_date)}: Large credit ₹{amount:,.0f} from "
                f"{_short_source(narr)} (possible loan disbursal) — "
                f"₹{total_out:,.0f} ({pct_out:.0f}%) redistributed across "
                f"{len(outflows)} outflows within 48h: {out_str}"
            ),
        })

    return events


def _detect_post_disbursement_usage(df: pd.DataFrame, salary_amount: float) -> list:
    """Analyse spending after each detected loan disbursement credit.

    For every credit that looks like a loan disbursal (keyword or lender match),
    gather all debits in a configurable window and:
      1. Group debits by recipient name.
      2. Check whether the total debits to concentrated recipients ≈ disbursed amount
         (suggests the loan was taken on behalf of / diverted to someone else).
      3. Build a descriptive event with top recipients and amounts.

    Complements _detect_loan_redistribution (which requires ≥2 outflows ≥30%).
    This detector focuses on *who* received the money and whether the sum is
    approximately equal to the loan amount.
    """
    import config.thresholds as T

    events = []

    min_amount = max(T.POST_DISB_MIN_AMOUNT, salary_amount * 2.0) if salary_amount > 0 else T.POST_DISB_MIN_AMOUNT
    window = timedelta(days=T.POST_DISB_WINDOW_DAYS)

    credits = df[df["dr_cr_indctor"] == "C"].copy()
    debits  = df[df["dr_cr_indctor"] == "D"].copy()

    if credits.empty or debits.empty:
        return events

    from tools.rules import is_loan_disbursal

    for _, row in credits[credits["tran_amt_in_ac"] >= min_amount].iterrows():
        narr   = _narr_upper(row)
        amount = float(row["tran_amt_in_ac"])

        # Must look like a loan disbursement — canonical narration rule
        # (l2=None keeps this a pure keyword/lender check).
        if not is_loan_disbursal(None, narr):
            continue

        txn_date   = row["tran_date"]
        window_end = txn_date + window

        # Gather post-disbursement debits
        post_debits = debits[
            (debits["tran_date"] >= txn_date) &
            (debits["tran_date"] <= window_end) &
            (debits["tran_amt_in_ac"] >= T.POST_DISB_MIN_DEBIT)
        ]

        if post_debits.empty:
            continue

        # Group debits by recipient
        recipient_totals: dict = {}  # name -> total amount
        recipient_txns: dict = {}    # name -> count
        for _, drow in post_debits.iterrows():
            d_narr = str(drow.get("tran_partclr", ""))
            d_amt  = float(drow["tran_amt_in_ac"])
            name   = _extract_name_from_narration(d_narr) or "Unknown"
            recipient_totals[name] = recipient_totals.get(name, 0.0) + d_amt
            recipient_txns[name]   = recipient_txns.get(name, 0) + 1

        total_debited = sum(recipient_totals.values())
        if total_debited == 0:
            continue

        # Sort recipients by total amount descending
        sorted_recipients = sorted(
            recipient_totals.items(), key=lambda x: x[1], reverse=True
        )

        # Check concentration: do top recipients account for ≥ threshold of disbursement?
        top_sum = 0.0
        top_recipients = []
        for name, amt in sorted_recipients:
            if name == "Unknown":
                continue
            top_sum += amt
            top_recipients.append((name, amt, recipient_txns.get(name, 1)))
            if len(top_recipients) >= 5:
                break

        concentration_pct = top_sum / amount if amount > 0 else 0

        # Check if total debited ≈ disbursed amount
        ratio = total_debited / amount if amount > 0 else 0
        amounts_match = abs(ratio - 1.0) <= T.POST_DISB_MATCH_TOLERANCE

        # Must have meaningful outflow — either concentrated or matching total
        if concentration_pct < T.POST_DISB_CONCENTRATION_PCT and not amounts_match:
            continue

        # Build description
        source = _short_source(narr)
        recip_parts = []
        for name, amt, count in top_recipients[:4]:
            recip_parts.append(f"{name}: ₹{amt:,.0f} ({count} txn)")
        recip_str = "; ".join(recip_parts) if recip_parts else "unidentified recipients"

        # Determine significance
        if amounts_match and concentration_pct >= T.POST_DISB_CONCENTRATION_PCT:
            significance = "high"
        elif amounts_match or concentration_pct >= T.POST_DISB_CONCENTRATION_PCT:
            significance = "high"
        else:
            significance = "medium"

        desc = (
            f"{_month_label(txn_date)}: Loan disbursement ₹{amount:,.0f} from {source} — "
            f"₹{total_debited:,.0f} ({ratio:.0%}) debited within {T.POST_DISB_WINDOW_DAYS} days. "
        )
        if amounts_match:
            desc += f"Debits ≈ disbursed amount. "
        if top_recipients:
            desc += f"Top recipients: {recip_str}"

        events.append({
            "type":        "post_disbursement_usage",
            "date":        str(txn_date.date()),
            "month_label": _month_label(txn_date),
            "amount":      amount,
            "significance": significance,
            "description": desc,
            # Extra fields for checklist / downstream consumption
            "_disbursed_amount": amount,
            "_total_debited":    round(total_debited, 2),
            "_concentration_pct": round(concentration_pct * 100, 1),
            "_amounts_match":    amounts_match,
            "_top_recipients":   [(n, round(a, 2)) for n, a, _ in top_recipients[:5]],
        })

    return events


def _detect_self_transfer_post_salary(
    df: pd.DataFrame,
    salary_txns: list,
    salary_amount: float,
    customer_name: Optional[str],
) -> list:
    """Detect salary received then quickly self-transferred to own account (≥40%, within 3 days).

    Complements _detect_post_salary_routing (which needs 2+ distinct recipients).
    This fires for the single-self-transfer conduit pattern.
    """
    events = []
    if not salary_txns or salary_amount <= 0:
        return events

    debits   = df[df["dr_cr_indctor"] == "D"].copy()
    name_pfx = (customer_name or "").upper()[:6].strip() or None

    # Track months already flagged to avoid duplicates within same salary month
    flagged_months: set = set()

    for sal_txn in salary_txns:
        try:
            sal_date = pd.to_datetime(sal_txn["date"])
            sal_amt  = float(sal_txn.get("amount", salary_amount) or salary_amount)
        except (ValueError, KeyError, TypeError):
            continue
        if sal_amt <= 0:
            sal_amt = salary_amount

        month_key = sal_date.strftime("%Y-%m")
        if month_key in flagged_months:
            continue

        window = debits[
            (debits["tran_date"] >= sal_date) &
            (debits["tran_date"] <= sal_date + timedelta(days=3)) &
            (debits["tran_amt_in_ac"] >= sal_amt * 0.40)
        ]

        for _, row in window.iterrows():
            narr = _narr_upper(row)
            if _is_self(row, name_pfx):
                amt  = float(row["tran_amt_in_ac"])
                pct  = amt / sal_amt * 100
                days = int((row["tran_date"] - sal_date).days)
                events.append({
                    "type":        "self_transfer_post_salary",
                    "date":        str(sal_date.date()),
                    "month_label": _month_label(sal_date),
                    "amount":      round(amt, 2),
                    "significance": "high",
                    "description": (
                        f"{_month_label(sal_date)}: Self-transfer after salary — "
                        f"₹{amt:,.0f} ({pct:.0f}% of ₹{sal_amt:,.0f} salary) "
                        f"transferred to own account {days} day(s) after credit "
                        f"({str(row.get('tran_partclr', ''))[:60]})"
                    ),
                })
                flagged_months.add(month_key)
                break  # one event per salary month

    return events


def _detect_round_trips(df: pd.DataFrame) -> list:
    """Detect money sent and received back within 7 days (same name, ±15% amount)."""
    events = []
    min_amount = 10000

    debits  = df[(df["dr_cr_indctor"] == "D") & (df["tran_amt_in_ac"] >= min_amount)].copy()
    credits = df[(df["dr_cr_indctor"] == "C") & (df["tran_amt_in_ac"] >= min_amount)].copy()

    seen = set()

    for _, drow in debits.iterrows():
        d_amt  = float(drow["tran_amt_in_ac"])
        d_date = drow["tran_date"]
        d_name = _extract_name_from_narration(str(drow.get("tran_partclr", "")))

        if not d_name:
            continue

        lo, hi = d_amt * 0.85, d_amt * 1.15
        window_credits = credits[
            (credits["tran_date"] >= d_date - timedelta(days=7)) &
            (credits["tran_date"] <= d_date + timedelta(days=7)) &
            (credits["tran_amt_in_ac"] >= lo) &
            (credits["tran_amt_in_ac"] <= hi)
        ]

        for _, crow in window_credits.iterrows():
            c_name = _extract_name_from_narration(str(crow.get("tran_partclr", "")))
            if not c_name:
                continue
            # Same name appears in both directions
            if d_name.lower()[:6] != c_name.lower()[:6]:
                continue

            key = (str(d_date.date()), d_name[:10])
            if key in seen:
                continue
            seen.add(key)

            c_amt  = float(crow["tran_amt_in_ac"])
            days   = abs((crow["tran_date"] - d_date).days)
            events.append({
                "type":        "round_trip",
                "date":        str(d_date.date()),
                "month_label": _month_label(d_date),
                "amount":      d_amt,
                "significance": "medium",
                "description": (
                    f"{_month_label(d_date)}: Possible round-trip — "
                    f"₹{d_amt:,.0f} sent to {d_name}, "
                    f"₹{c_amt:,.0f} received back within {days} day(s). "
                    "May indicate informal lending or circular transaction."
                ),
            })
            break  # one match per debit

    return events


# ---------------------------------------------------------------------------
# Layer 3: Inflow anomaly detectors
# ---------------------------------------------------------------------------

def _detect_inflow_spike(df: pd.DataFrame, salary_amount: float) -> list:
    """Detect months where total credit inflow exceeds 2× the median monthly inflow.

    Identifies the specific top credit transaction(s) that caused the spike
    and classifies each source (lender, employer, government, individual).
    """
    events = []

    credits, monthly_totals, median_credit = _compute_monthly_credit_stats(df)
    if credits is None:
        return events

    threshold = median_credit * 2.0

    for period, month_total in monthly_totals.items():
        if month_total <= threshold:
            continue

        spike_ratio = month_total / median_credit

        # Get top contributing credits for this month
        month_credits = credits[credits["_month"] == period].sort_values(
            "tran_amt_in_ac", ascending=False
        )
        top_txns = []
        for _, row in month_credits.head(3).iterrows():
            amt = float(row["tran_amt_in_ac"])
            narr = str(row.get("tran_partclr", ""))
            source = _classify_credit_source(narr)
            top_txns.append(f"₹{amt:,.0f} ({source})")

        top_str = ", ".join(top_txns)
        month_start = period.to_timestamp()

        events.append({
            "type":        "inflow_spike",
            "date":        str(month_start.date()),
            "month_label": _month_label(month_start),
            "amount":      round(float(month_total), 2),
            "significance": "high",
            "description": (
                f"{_month_label(month_start)}: Monthly inflow ₹{month_total:,.0f} "
                f"is {spike_ratio:.1f}× the median (₹{median_credit:,.0f}) — "
                f"top credits: {top_str}"
            ),
        })

    return events


def _detect_large_single_credit(df: pd.DataFrame, salary_amount: float) -> list:
    """Detect single credits exceeding 100% of the median monthly credit total.

    Skips routine salary credits. Groups qualifying credits by month and
    emits one event per month listing the top anomalous credits.
    """
    events = []

    credits, monthly_totals, median_credit = _compute_monthly_credit_stats(df)
    if credits is None:
        return events

    # Filter to credits exceeding median (with 10% buffer to avoid borderline noise)
    large = credits[credits["tran_amt_in_ac"] > median_credit * 1.10].copy()
    if large.empty:
        return events

    # Skip routine salary credits — by narration keywords (works even without salary_amount)
    salary_kws = SALARY_CREDIT_FRAGMENTS

    def _is_routine_salary(row):
        narr = str(row.get("tran_partclr", "")).upper()
        if not any(kw in narr for kw in salary_kws):
            return False
        # If salary_amount is known, also check amount is within ±30% of it
        if salary_amount > 0:
            amt = float(row["tran_amt_in_ac"])
            return salary_amount * 0.70 <= amt <= salary_amount * 1.30
        # No salary_amount known — trust the narration keyword alone
        return True

    mask = large.apply(_is_routine_salary, axis=1)
    large = large[~mask]

    if large.empty:
        return events

    # Group by month, build one event per month
    month_groups = large.groupby("_month")
    month_events = []

    for period, group in month_groups:
        group_sorted = group.sort_values("tran_amt_in_ac", ascending=False)
        top_txns = []
        largest_amt = 0.0
        for _, row in group_sorted.head(3).iterrows():
            amt = float(row["tran_amt_in_ac"])
            if amt > largest_amt:
                largest_amt = amt
            narr = str(row.get("tran_partclr", ""))
            source = _classify_credit_source(narr)
            ratio = amt / median_credit
            top_txns.append(f"₹{amt:,.0f} ({ratio:.1f}× median, {source})")

        count = len(group_sorted)
        txn_str = ", ".join(top_txns)
        month_start = period.to_timestamp()

        desc_prefix = (
            f"{_month_label(month_start)}: {count} credit(s) exceeding "
            f"monthly median (₹{median_credit:,.0f})"
            if count > 1 else
            f"{_month_label(month_start)}: Single credit exceeding "
            f"monthly median (₹{median_credit:,.0f})"
        )

        month_events.append({
            "type":        "large_single_credit",
            "date":        str(month_start.date()),
            "month_label": _month_label(month_start),
            "amount":      round(largest_amt, 2),
            "significance": "high",
            "description":  f"{desc_prefix} — {txn_str}",
            "_sort_key":    largest_amt,  # for capping
        })

    # Cap at 5 events, keep months with largest credits
    month_events.sort(key=lambda e: e["_sort_key"], reverse=True)
    for ev in month_events[:5]:
        ev.pop("_sort_key", None)
        events.append(ev)

    return events


# ---------------------------------------------------------------------------
# Layer 2.5: Credit-to-spend timing dependency
# ---------------------------------------------------------------------------

def _detect_credit_spend_dependency(
    df: pd.DataFrame,
    salary_txns: list,
    salary_amount: float,
    customer_name: Optional[str],
) -> list:
    """Detect spending dependency on credit receipt.

    For each significant credit, measure what fraction of that credit
    is consumed by debits within a short window. Flags "funded spender"
    patterns where the customer can only spend after receiving funds.

    Self-transfers are included in the debit total but annotated separately.
    Skips events where the only debit is a single self-transfer (covered by
    the existing self_transfer_post_salary detector).
    """
    import config.thresholds as T

    events = []

    # Need monthly credit stats for dynamic threshold
    _, _, median_monthly_credit = _compute_monthly_credit_stats(df)
    if median_monthly_credit is None:
        # Fall back to a static floor if insufficient monthly data
        min_credit = T.CREDIT_SPEND_MIN_AMOUNT
    else:
        min_credit = max(T.CREDIT_SPEND_MIN_AMOUNT,
                         median_monthly_credit * T.CREDIT_SPEND_MIN_RATIO)

    window = timedelta(days=T.CREDIT_SPEND_WINDOW_DAYS)

    # All credits sorted by date
    credits = df[
        (df["dr_cr_indctor"] == "C") &
        (df["tran_amt_in_ac"] >= min_credit)
    ].sort_values("tran_date").copy()

    if credits.empty:
        return events

    # All debits sorted by date
    debits = df[df["dr_cr_indctor"] == "D"].sort_values("tran_date").copy()
    if debits.empty:
        return events

    # Build customer name prefix for self-transfer detection
    name_prefix = customer_name.upper().split()[0] if customer_name else None

    # Merge nearby credits (within 2 days) to avoid double-counting debits
    credit_clusters = []
    for _, row in credits.iterrows():
        cr_date = row["tran_date"]
        cr_amt = float(row["tran_amt_in_ac"])
        cr_narr = str(row.get("tran_partclr", ""))

        if credit_clusters and (cr_date - credit_clusters[-1]["end_date"]).days <= 2:
            # Extend existing cluster
            credit_clusters[-1]["total_amount"] += cr_amt
            credit_clusters[-1]["end_date"] = cr_date
            credit_clusters[-1]["narrations"].append(cr_narr)
            credit_clusters[-1]["count"] += 1
        else:
            credit_clusters.append({
                "start_date": cr_date,
                "end_date": cr_date,
                "total_amount": cr_amt,
                "narrations": [cr_narr],
                "count": 1,
            })

    for cluster in credit_clusters:
        cr_start = cluster["start_date"]
        cr_end = cluster["end_date"]
        cr_amount = cluster["total_amount"]
        window_end = cr_end + window

        # Debits within [cr_start, cr_end + window_days]
        window_debits = debits[
            (debits["tran_date"] >= cr_start) &
            (debits["tran_date"] <= window_end)
        ]

        if window_debits.empty:
            continue

        # Break down debits: self-transfer vs third-party
        self_transfer_amt = 0.0
        third_party_amt = 0.0
        self_transfer_count = 0
        third_party_count = 0

        for _, drow in window_debits.iterrows():
            amt = float(drow["tran_amt_in_ac"])
            narr = str(drow.get("tran_partclr", ""))

            if _is_self(drow, name_prefix):
                self_transfer_amt += amt
                self_transfer_count += 1
            else:
                third_party_amt += amt
                third_party_count += 1

        total_debit = self_transfer_amt + third_party_amt
        spend_ratio = total_debit / cr_amount if cr_amount > 0 else 0

        if spend_ratio < T.CREDIT_SPEND_MEDIUM_THRESHOLD:
            continue

        # Skip if the only debit is a single self-transfer (existing detector covers this)
        if third_party_count == 0 and self_transfer_count == 1:
            continue

        # Determine significance
        if spend_ratio >= T.CREDIT_SPEND_HIGH_THRESHOLD:
            significance = "high"
        else:
            significance = "medium"

        # Classify credit source (use first narration in cluster)
        source = _classify_credit_source(cluster["narrations"][0] if cluster["narrations"] else "")

        # Build debit breakdown string
        parts = []
        if self_transfer_count > 0:
            parts.append(f"₹{self_transfer_amt:,.0f} self-transfer")
        if third_party_count > 0:
            parts.append(f"₹{third_party_amt:,.0f} third-party ({third_party_count} txn)")
        debit_str = ", ".join(parts)

        total_debit_count = self_transfer_count + third_party_count
        cr_str = f"₹{cr_amount:,.0f}"
        if cluster["count"] > 1:
            cr_str += f" ({cluster['count']} credits)"

        desc = (
            f"{_month_label(cr_start)}: {cr_str} received ({source}) — "
            f"₹{total_debit:,.0f} ({spend_ratio:.0%}) spent within "
            f"{T.CREDIT_SPEND_WINDOW_DAYS} days "
            f"({total_debit_count} debits: {debit_str})"
        )

        events.append({
            "type": "credit_spend_dependency",
            "date": str(cr_start.date()),
            "month_label": _month_label(cr_start),
            "amount": round(cr_amount, 2),
            "significance": significance,
            "description": desc,
        })

    return events


# ---------------------------------------------------------------------------
# Deduplication and formatting
# ---------------------------------------------------------------------------

def _deduplicate(events: list) -> list:
    """Remove duplicate events of the same type on the same date."""
    seen = set()
    out  = []
    for e in events:
        key = (e["type"], e.get("date", "")[:7])  # same type + same month
        # For recurring (min_months) events allow only one per type
        if e.get("month_label", "").startswith("Ongoing"):
            key = (e["type"],)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def format_events_for_prompt(events: list) -> str:
    """Format event list as a structured block for the LLM prompt."""
    if not events:
        return ""

    sig_label = {"high": "HIGH", "medium": "MEDIUM", "positive": "POSITIVE"}
    lines = ["DETECTED TRANSACTION EVENTS [include in summary with specific dates/amounts]:"]
    for e in events:
        tag  = sig_label.get(e["significance"], "INFO")
        line = f"  [{tag:8s}] {e['description']}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ATM withdrawal analysis
# ---------------------------------------------------------------------------

def _extract_atm_address(narration: str) -> Optional[str]:
    """Extract address from ATM narration.

    Format: ATL/<terminal>/<id>/<ADDRESS>/<time>
    The address sits between the 3rd '/' and the last '/'.

    Example:
        "ATL/2543/81165/SANJAY NAGAR NIVARU/21:03"  →  "Sanjay Nagar Nivaru"
    """
    parts = narration.split("/")
    if len(parts) >= 5:
        # Everything between 3rd '/' and last '/' is the address
        address = "/".join(parts[3:-1]).strip()
        if address:
            return address.title()
    return None


def _detect_atm_withdrawals(df: pd.DataFrame) -> list:
    """Analyse ATM withdrawal patterns: trend and location extraction.

    Compares the total ATM withdrawal amount in the last 3 months to the
    first 3 months.  Extracts ATM addresses as likely nearby locations.
    """
    events = []
    debits = df[df["dr_cr_indctor"] == "D"].copy()
    if debits.empty:
        return events

    # Canonical ATM rule (tran_type column OR ATM narration keyword)
    from tools.rules import is_atm_debit
    atm_mask = debits.apply(is_atm_debit, axis=1)
    atm_txns = debits[atm_mask].copy()

    if atm_txns.empty:
        return events

    atm_txns["tran_date"] = pd.to_datetime(atm_txns["tran_date"], errors="coerce")
    atm_txns = atm_txns.dropna(subset=["tran_date"])

    if atm_txns.empty:
        return events

    total_count = len(atm_txns)
    total_amount = float(atm_txns["tran_amt_in_ac"].sum())

    # Split months into first-half and last-half
    atm_txns["_period"] = atm_txns["tran_date"].dt.to_period("M")
    all_months = sorted(atm_txns["_period"].unique())
    n_months = len(all_months)

    if n_months >= 2:
        mid = n_months // 2  # e.g. 6 months → mid=3, first=[0,1,2] last=[3,4,5]
        first_months = set(all_months[:mid])
        last_months = set(all_months[mid:])

        first_half = atm_txns[atm_txns["_period"].isin(first_months)]
        last_half = atm_txns[atm_txns["_period"].isin(last_months)]

        first_amt = float(first_half["tran_amt_in_ac"].sum())
        last_amt = float(last_half["tran_amt_in_ac"].sum())
        first_count = len(first_half)
        last_count = len(last_half)

        if first_amt > 0:
            change_pct = ((last_amt - first_amt) / first_amt) * 100
        else:
            change_pct = 100.0 if last_amt > 0 else 0.0

        is_elevated = last_amt > first_amt and change_pct > 20
    else:
        first_amt = last_amt = 0
        first_count = last_count = 0
        change_pct = 0
        is_elevated = False

    # Extract addresses with frequency counts
    from collections import Counter
    from utils.narration_utils import fuzzy_group_keys
    raw_counts: Counter = Counter()
    for _, row in atm_txns.iterrows():
        narr = str(row.get("tran_partclr", ""))
        addr = _extract_atm_address(narr)
        if addr:
            raw_counts[addr] += 1
    # Fuzzy-group near-duplicate addresses (e.g. trailing-digit / casing variants)
    # so frequency reflects real-world unique locations.
    rep_map = fuzzy_group_keys(list(raw_counts.keys())) if raw_counts else {}
    grouped_counts: Counter = Counter()
    for addr, cnt in raw_counts.items():
        rep = rep_map.get(addr, addr)
        grouped_counts[rep] += cnt
    addresses = set(grouped_counts.keys())
    # Most frequent ATM only if used more than twice (≥3 occurrences).
    # Tie-break: highest count, then alphabetical.
    top_address = None
    if grouped_counts:
        best_addr, best_count = sorted(
            grouped_counts.items(), key=lambda kv: (-kv[1], kv[0])
        )[0]
        if best_count >= 1:
            top_address = {"address": best_addr, "count": best_count}

    # Build description
    desc = (
        f"ATM withdrawals: {total_count} transactions totalling ₹{total_amount:,.0f}. "
    )
    if n_months >= 2:
        desc += (
            f"First {len(set(all_months[:n_months // 2]))} months: "
            f"{first_count} txns ₹{first_amt:,.0f}; "
            f"Last {len(set(all_months[n_months // 2:]))} months: "
            f"{last_count} txns ₹{last_amt:,.0f} "
            f"({'+' if change_pct >= 0 else ''}{change_pct:.0f}%). "
        )
    if addresses:
        desc += "Locations: " + ", ".join(sorted(addresses))

    significance = "medium" if is_elevated else "neutral"

    events.append({
        "type":        "atm_withdrawal",
        "date":        str(atm_txns["tran_date"].min().date()),
        "month_label": f"{total_count} ATM withdrawals across {n_months} month(s)",
        "amount":      round(total_amount, 2),
        "significance": significance,
        "description": desc,
        # Extra fields for checklist consumption
        "_total_count":  total_count,
        "_total_amount": round(total_amount, 2),
        "_first_half_amount": round(first_amt, 2),
        "_last_half_amount":  round(last_amt, 2),
        "_first_half_count":  first_count,
        "_last_half_count":   last_count,
        "_change_pct":   round(change_pct, 1),
        "_is_elevated":  is_elevated,
        "_addresses":    sorted(addresses),
        "_top_address":  top_address,
    })

    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_events(customer_id: int, rg_salary_data: Optional[dict] = None) -> list:
    """Detect semantic events from raw transactions for a customer.

    Loads rg_salary_data internally if not provided.
    Returns list of event dicts sorted by significance (high → medium → positive).

    Each event dict:
        type, date, month_label, amount (optional), significance, description
    """
    # Load salary data
    if rg_salary_data is None:
        try:
            rg_salary_data = load_rg_salary_data(customer_id) or {}
        except Exception as exc:
            logger.warning("event_detector: load_rg_salary_data failed for %s: %s", customer_id, exc)
            rg_salary_data = {}

    rg_sal          = rg_salary_data.get("rg_sal") or {}
    salary_txns     = rg_sal.get("transactions", [])
    salary_amount   = float(rg_sal.get("salary_amount", 0) or 0)

    # Load raw transactions
    try:
        df      = get_transactions_df()
        cust_df = df[df["cust_id"] == customer_id].copy()
    except Exception as exc:
        logger.warning("event_detector: get_transactions_df failed for %s: %s", customer_id, exc)
        return []

    if cust_df.empty:
        return []

    cust_df["tran_date"] = pd.to_datetime(cust_df["tran_date"], errors="coerce")
    cust_df = cust_df.dropna(subset=["tran_date"])

    # Customer name for self-transfer detection (load from df itself)
    customer_name = None
    if "prty_name" in cust_df.columns and len(cust_df) > 0:
        raw_name = cust_df["prty_name"].iloc[0]
        if raw_name and str(raw_name).lower() not in ("nan", "none", ""):
            customer_name = str(raw_name)

    # ── Layer 1: Keyword rules ─────────────────────────────────────────────
    events = _apply_keyword_rules(cust_df)

    # ── Layer 2: Custom multi-step detectors ──────────────────────────────
    try:
        events += _detect_self_transfer_post_salary(cust_df, salary_txns, salary_amount, customer_name)
    except Exception as exc:
        logger.warning("event_detector: self_transfer_post_salary failed: %s", exc)

    try:
        events += _detect_post_salary_routing(cust_df, salary_txns, salary_amount, customer_name)
    except Exception as exc:
        logger.warning("event_detector: post_salary_routing failed: %s", exc)

    try:
        events += _detect_loan_redistribution(cust_df, salary_amount)
    except Exception as exc:
        logger.warning("event_detector: loan_redistribution failed: %s", exc)

    try:
        events += _detect_post_disbursement_usage(cust_df, salary_amount)
    except Exception as exc:
        logger.warning("event_detector: post_disbursement_usage failed: %s", exc)

    try:
        events += _detect_round_trips(cust_df)
    except Exception as exc:
        logger.warning("event_detector: round_trips failed: %s", exc)

    # ── Layer 2.5: Credit-to-spend timing dependency ─────────────────────
    try:
        events += _detect_credit_spend_dependency(cust_df, salary_txns, salary_amount, customer_name)
    except Exception as exc:
        logger.warning("event_detector: credit_spend_dependency failed: %s", exc)

    # ── Layer 3: Inflow anomaly detectors ─────────────────────────────────
    try:
        events += _detect_inflow_spike(cust_df, salary_amount)
    except Exception as exc:
        logger.warning("event_detector: inflow_spike failed: %s", exc)

    try:
        events += _detect_large_single_credit(cust_df, salary_amount)
    except Exception as exc:
        logger.warning("event_detector: large_single_credit failed: %s", exc)

    try:
        events += _detect_atm_withdrawals(cust_df)
    except Exception as exc:
        logger.warning("event_detector: atm_withdrawals failed: %s", exc)

    # Deduplicate and sort
    events = _deduplicate(events)
    events.sort(key=lambda e: (_SIG_ORDER.get(e["significance"], 9), e.get("date", "")))

    return events
