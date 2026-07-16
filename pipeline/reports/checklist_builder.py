"""Deterministic banking checklist builder.

Computes the review checklist (risk / FCU / fraud items) from a built
CustomerReport plus raw transactions. Lives in the build layer so renderers
stay pure: they consume ``CustomerReport.checklist`` and never compute it.

Every check produces one ``{label, checked, severity, detail}`` item via
``make_item``. Event-presence checks go through ``presence_item``; the heavier
transaction-level checks are individual named functions guarded by
``utils.helpers.safe_call`` so a failure in one is logged and skips only that
item instead of the whole block.
"""

from typing import Optional

import numpy as np
import pandas as pd

import config.thresholds as T
from schemas.customer_report import CustomerReport
from utils.helpers import safe_call


# Item primitives

def make_item(label, checked, severity, detail=None) -> dict:
    """Build one checklist item dict — the single schema shared by every check."""
    return {"label": label, "checked": checked, "severity": severity, "detail": detail}


def events_of_type(events, etype) -> list:
    """All events whose ``type`` equals ``etype``."""
    return [e for e in events if e.get("type") == etype]


def top_amount_description(events):
    """Description of the highest-amount event in the list (detail selector)."""
    return max(events, key=lambda e: float(e.get("amount") or 0)).get("description")


def presence_item(label, events, severity_present, severity_absent="neutral", detail_fn=None) -> dict:
    """Standard "is there an event of this kind?" item. """
    if not events: return make_item(label, False, severity_absent, None)
    detail = detail_fn(events) if detail_fn else events[0].get("description")
    return make_item(label, True, severity_present, detail)


# Public entry point
def compute_checklist(customer_report: Optional[CustomerReport], cust_df=None) -> dict:
    """Compute yes/no checklist items from existing report data.

    Returns a dict with key ``banking``: a list of {label, checked, severity,
    detail} dicts. ``cust_df`` is the customer's raw transaction frame; the
    report builder already holds it and threads it in to avoid a second
    load+filter. When omitted it is loaded on demand.
    """
    events = (customer_report.events or []) if customer_report else []
    if cust_df is None and customer_report is not None:
        cust_df = safe_call(load_customer_frame, customer_report.meta.customer_id)

    # K2 loan-disbursement events: explicit disbursal / redistribution, else a
    # large single credit whose narration mentions a lender or loan.
    loan_events = (events_of_type(events, "loan_disbursal") 
                  or events_of_type(events, "loan_redistribution_suspect") 
                  or [e for e in events_of_type(events, "large_single_credit") if "lender" in str(e.get("description", "")).lower()
                  or "loan" in str(e.get("description", "")).lower()])

    items = [
        presence_item("ECS / NACH bounces", events_of_type(events, "ecs_bounce"), "high"),
        presence_item("Loan disbursement detected", loan_events, "high"),                 # K2
        post_disbursement_item(events),                                                   # K3
        salary_item(customer_report),                                                     # K4
        presence_item("Big debits after salary credit (≥40% within 3d)", events_of_type(events, "self_transfer_post_salary"), "medium", detail_fn=top_amount_description),
        presence_item("Circular entry (mirror credit/debit)", events_of_type(events, "round_trip"), "high", severity_absent="positive", detail_fn=top_amount_description),
        safe_call(both_side_counterparties_item, cust_df, default=make_item("Counterparties on both credit & debit", False, "neutral", None)),
        emi_item(customer_report),                                                        # K6
        presence_item("NACH mandate EMI detected", events_of_type(events, "mandate_emi"), "medium"),
        rent_item(customer_report),
        presence_item("Credit card bill payments", events_of_type(events, "cc_payment"), "positive"),
        presence_item("Land purchase payments", events_of_type(events, "land_payment"), "medium"),
        atm_withdrawal_item(events),
        safe_call(percentile_outliers_item, cust_df),
        safe_call(automated_txn_item, cust_df),
        safe_call(mode_shift_item, cust_df),
    ]
    
    banking_items = [it for it in items if it is not None]  # K13–K15 return None when the transaction frame is empty/unavailable.

    emerging = emerging_merchants_item(customer_report)                                   # K16
    if emerging is not None: banking_items.append(emerging)
    return {"banking": banking_items}


# Report-field checks (K4 / K6 / K8 / K16)
def salary_item(customer_report) -> dict:
    """K4: salary detected in banking."""
    has_salary = customer_report and customer_report.salary is not None
    detail = None
    if has_salary:
        sal = customer_report.salary
        detail = f"₹{sal.avg_amount:,.0f} avg ({sal.frequency} transactions)"
    return make_item("Salary detected in banking", has_salary, "positive" if has_salary else "neutral", detail)


def emi_item(customer_report) -> dict:
    """K6: EMI obligations present."""
    has_emis = customer_report and customer_report.emis and len(customer_report.emis) > 0
    detail = None
    if has_emis:
        total_emi = sum(e.amount for e in customer_report.emis)
        detail = f"₹{total_emi:,.0f} total across {len(customer_report.emis)} lender(s)"
    return make_item("EMI obligations present", bool(has_emis), "medium" if has_emis else "neutral", detail)


def rent_item(customer_report) -> dict:
    """K8: rent payments present."""
    has_rent = customer_report and customer_report.rent is not None
    detail = (f"₹{customer_report.rent.amount:,.0f} ({customer_report.rent.frequency} transactions)"
              if has_rent else None)
    return make_item("Rent payments present", bool(has_rent), "neutral", detail)


def emerging_merchants_item(customer_report):
    """K16: merchants new in recent months (absent before). None if none."""
    if not (customer_report and customer_report.merchant_features):
        return None
    em = customer_report.merchant_features.get("emerging_merchants", {})
    em_list = em.get("emerging_merchants", [])
    if not em_list:
        return None
    names = ", ".join(e["name"] for e in em_list[:3])
    return make_item("Emerging merchants detected", True, "medium", f"{len(em_list)} new: {names}")

# Event-derived checks with custom logic (K3 / K12)

def disbursement_severity(event) -> str:
    """K3 severity: 'high' when diverted amounts match the disbursal or the top
    recipients concentrate at/above the configured share, else 'medium'."""
    if event.get("_amounts_match", False):  return "high"
    conc_pct = event.get("_concentration_pct", 0)
    return "high" if conc_pct >= T.POST_DISB_CONCENTRATION_PCT * 100 else "medium"


def post_disbursement_item(events) -> dict:
    """K3: post-disbursement fund diversion."""
    disb = events_of_type(events, "post_disbursement_usage")
    if not disb: return make_item("Post-disbursement fund diversion", False, "neutral", None)
    ev = disb[0]
    return make_item("Post-disbursement fund diversion", True, disbursement_severity(ev), ev.get("description"))


def atm_withdrawal_item(events) -> dict:
    """K12: ATM withdrawal trend + most-frequent location."""
    label = "ATM withdrawals elevated"
    atm = events_of_type(events, "atm_withdrawal")
    if not atm: return make_item(label, False, "neutral", None)
    ev = atm[0]
    is_elevated = ev.get("_is_elevated", False)
    top = ev.get("_top_address")
    detail = ev.get("description", "")
    if top: detail += f" | Most frequent ATM: {top['address']} ({top['count']} times)"
    return make_item(label, is_elevated, "medium" if is_elevated else "neutral", detail)


# Transaction-level checks (K5c / K13 / K14 / K15) — operate on the raw frame
def load_customer_frame(customer_id):
    """Load and filter the raw transaction rows for one customer (fallback when
    the caller does not already hold the frame)."""
    from data.loader import get_transactions_df
    df = get_transactions_df()
    return df[df["cust_id"] == customer_id].copy()


def both_side_counterparties_item(cust_df) -> dict:
    """K5c: counterparties appearing on both the credit and debit sides."""
    label = "Counterparties on both credit & debit"
    if cust_df is None or cust_df.empty:
        return make_item(label, False, "neutral", None)
    from utils.narration_utils import extract_recipient_name
    cdf = cust_df.copy()
    cdf["_who"] = cdf["tran_partclr"].astype(str).map(lambda s: (extract_recipient_name(s) or "").strip().upper())
    cdf = cdf[cdf["_who"] != ""]
    cdf["_amt"] = cdf["tran_amt_in_ac"].astype(float).abs()
    cr = cdf[cdf["dr_cr_indctor"] == "C"]
    dr = cdf[cdf["dr_cr_indctor"] == "D"]
    shared = set(cr["_who"]).intersection(set(dr["_who"]))

    if not shared:
        return make_item(label, False, "neutral", None)
    sub = cdf[cdf["_who"].isin(shared)]
    top_party = sub.groupby("_who")["_amt"].sum().sort_values(ascending=False).index[0]
    cr_amt = float(cr.loc[cr["_who"] == top_party, "_amt"].sum())
    dr_amt = float(dr.loc[dr["_who"] == top_party, "_amt"].sum())
    detail = (
        f"{len(shared)} counterparties on both sides — top: "
        f"{top_party[:40]} (₹{cr_amt:,.0f} in / ₹{dr_amt:,.0f} out)"
    )
    return make_item(label, True, "medium", detail)


def percentile_outliers_item(cust_df):
    """K13: credits / debits above the 95th percentile. None if no frame."""
    if cust_df is None or cust_df.empty:
        return None
    from utils.narration_utils import extract_recipient_name, clean_narration
    amounts = cust_df["tran_amt_in_ac"].fillna(0).astype(float)
    directions = cust_df["dr_cr_indctor"].fillna("")
    outlier_parts = []
    for direction, label in [("C", "credit"), ("D", "debit")]:
        mask = directions == direction
        dir_amounts = amounts[mask]
        if len(dir_amounts) < 5:
            continue
        p95 = np.percentile(dir_amounts, 95)
        outliers = cust_df[mask & (amounts > p95)]
        for _, row in outliers.iterrows():
            narr = str(row.get("tran_partclr", ""))
            merchant = extract_recipient_name(narr) or clean_narration(narr) or "Unknown"
            amt = float(row.get("tran_amt_in_ac", 0))
            outlier_parts.append(f"{merchant}: ₹{amt:,.0f} ({label})")
    has_outliers = bool(outlier_parts)
    return make_item("Transactions above 95th percentile", has_outliers,
                     "medium" if has_outliers else "neutral",
                     "; ".join(outlier_parts[:5]) if has_outliers else None)


def automated_txn_item(cust_df):
    """K14: count of automated (NACH / mandate) debits & credits. None if no frame."""
    if cust_df is None or cust_df.empty: return None
    narr_upper = cust_df["tran_partclr"].fillna("").str.upper()
    directions = cust_df["dr_cr_indctor"].fillna("")
    auto_mask = narr_upper.str.contains("NACH|MANDATE", na=False, regex=True)
    auto_debits = int((auto_mask & (directions == "D")).sum())
    auto_credits = int((auto_mask & (directions == "C")).sum())
    auto_total = auto_debits + auto_credits
    detail = (f"{auto_total} total ({auto_debits} debits, {auto_credits} credits)" if auto_total > 0 else None)
    return make_item("Automated (NACH/mandate) transactions", auto_total > 0, "neutral", detail)


def infer_payment_mode(row) -> str:
    """Standardised payment mode (transaction "type") for one row: prefer the
    explicit ``tran_type`` column, else infer from the narration prefix."""
    tt = row.get("tran_type")
    if pd.notna(tt) and str(tt).strip():
        return str(tt).strip()
    nu = str(row.get("tran_partclr", "")).strip().upper()
    if "UPI" in nu: return "UPI"
    if "NEFT" in nu: return "NEFT"
    if "IMPS" in nu: return "IMPS"
    if "RTGS" in nu: return "RTGS"
    if "NACH-" in nu: return "NACH"
    if "MB:RECEIVED" in nu or "MB:SENT" in nu: return "Mobile Banking"
    if "IFT-" in nu: return "IFT"
    if nu.startswith("IB:RECEIVED FROM") or "IB:FUND" in nu: return "Internet Banking"
    if nu.startswith("FUND TRF FROM") or nu.startswith("FT FROM") or nu.startswith("FUNDS TRF FROM"): return "Funds Transfer"
    if nu.startswith("ATL/") or nu.startswith("ATW/"): return "ATM"
    if nu.startswith("PG "): return "Payment Gateway"
    if nu.startswith("PCD/"): return "Card Payment"
    if nu.startswith("CLG TO "): return "Cheque"
    return "Other"


def mode_shift_item(cust_df):
    """K15: shift in payment-mode distribution (recent vs earlier). None if no frame."""
    label = "Payment mode distribution shift"
    if cust_df is None or cust_df.empty:
        return None

    mode_col = cust_df.apply(infer_payment_mode, axis=1)
    dates = pd.to_datetime(cust_df["tran_date"], format="%Y-%m-%d", errors="coerce")
    periods = dates.dt.to_period("M")
    months_all = sorted(periods.dropna().unique())
    if len(months_all) < T.MODE_SHIFT_MIN_MONTHS:
        return make_item(label, False, "neutral", None)

    recent_set = set(months_all[-T.MODE_SHIFT_RECENT_MONTHS:])
    is_recent = periods.map(lambda m: m in recent_set if pd.notna(m) else False)
    earlier_mask = ~is_recent & periods.notna()
    recent_mask = is_recent
    if not (int(earlier_mask.sum()) >= T.MODE_SHIFT_MIN_TRANSACTIONS
            and int(recent_mask.sum()) >= T.MODE_SHIFT_MIN_TRANSACTIONS):
        return make_item(label, False, "neutral", None)

    e_dist = mode_col[earlier_mask].value_counts(normalize=True) * 100
    r_dist = mode_col[recent_mask].value_counts(normalize=True) * 100
    all_modes = sorted(set(e_dist.index) | set(r_dist.index))
    shifts = {}
    for m in all_modes:
        old = e_dist.get(m, 0.0)
        new = r_dist.get(m, 0.0)
        delta = new - old
        if abs(delta) >= T.MODE_SHIFT_THRESHOLD_PP:
            shifts[m] = (old, new, delta)
    if not shifts:
        return make_item(label, False, "neutral", None)

    parts = []
    for m, (old, new, delta) in sorted(shifts.items(), key=lambda x: -abs(x[1][2])):
        sign = "+" if delta > 0 else ""
        parts.append(f"{m}: {old:.0f}% → {new:.0f}% ({sign}{delta:.0f}pp)")
    return make_item(label, True, "medium", "; ".join(parts))
