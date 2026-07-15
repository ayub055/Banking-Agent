"""Deterministic banking checklist builder.

Computes the review checklist (risk / FCU / fraud items) from a built
CustomerReport plus raw transactions. Lives in the build layer so renderers
stay pure: they consume ``CustomerReport.checklist`` and never compute it.
"""

from typing import Optional

import numpy as np
import config.thresholds as T

from schemas.customer_report import CustomerReport


_BETTING_CATS = {"Digital_Betting_Gaming", "Betting_Gaming", "Betting", "Gaming"}


def compute_checklist(customer_report: Optional[CustomerReport]) -> dict:
    """Compute yes/no checklist items from existing report data.

    Returns dict with key ``banking``: a list of dicts
    {label, checked, severity, detail}.
    """
    banking_items: list = []
    events = (customer_report.events or []) if customer_report else []

    def _events_of_type(etype):
        return [e for e in events if e.get("type") == etype]

    # ── BANKING CHECKLIST ─────────────────────────────────────────

    # K1. ECS/NACH bounces
    bounces = _events_of_type("ecs_bounce")
    banking_items.append({
        "label": "ECS / NACH bounces",
        "checked": bool(bounces),
        "severity": "high" if bounces else "neutral",
        "detail": bounces[0].get("description") if bounces else None,
    })

    # K2. Loan disbursement detected
    loan_events = (_events_of_type("loan_disbursal")
                   or _events_of_type("loan_redistribution_suspect")
                   or [e for e in _events_of_type("large_single_credit")
                       if "lender" in str(e.get("description", "")).lower()
                       or "loan" in str(e.get("description", "")).lower()])
    banking_items.append({
        "label": "Loan disbursement detected",
        "checked": bool(loan_events),
        "severity": "high" if loan_events else "neutral",
        "detail": loan_events[0].get("description") if loan_events else None,
    })

    # K3. Post-disbursement fund usage
    disb_usage = _events_of_type("post_disbursement_usage")
    if disb_usage:
        ev = disb_usage[0]
        match_flag = ev.get("_amounts_match", False)
        conc_pct   = ev.get("_concentration_pct", 0)
        severity = "high" if match_flag else ("high" if conc_pct >= 50 else "medium")
        banking_items.append({
            "label": "Post-disbursement fund diversion",
            "checked": True,
            "severity": severity,
            "detail": ev.get("description"),
        })
    else:
        banking_items.append({
            "label": "Post-disbursement fund diversion",
            "checked": False,
            "severity": "neutral",
            "detail": None,
        })

    # K4. Salary detected
    has_salary = customer_report and customer_report.salary is not None
    sal_detail = None
    if has_salary:
        sal = customer_report.salary
        sal_detail = f"₹{sal.avg_amount:,.0f} avg ({sal.frequency} transactions)"
    banking_items.append({
        "label": "Salary detected in banking",
        "checked": has_salary,
        "severity": "positive" if has_salary else "neutral",
        "detail": sal_detail,
    })

    # K5. Big debits within 3 days of salary credit (≥40% of salary)
    # Reuses self_transfer_post_salary detector (window=3d, threshold=40%).
    self_transfers = _events_of_type("self_transfer_post_salary")
    st_detail = None
    if self_transfers:
        top = max(self_transfers, key=lambda e: float(e.get("amount") or 0))
        st_detail = top.get("description")
    banking_items.append({
        "label": "Big debits after salary credit (≥40% within 3d)",
        "checked": bool(self_transfers),
        "severity": "medium" if self_transfers else "neutral",
        "detail": st_detail,
    })

    # K5b. Circular entry — mirror credit/debit with same counterparty (round_trip events).
    round_trips = _events_of_type("round_trip")
    rt_detail = None
    if round_trips:
        top = max(round_trips, key=lambda e: float(e.get("amount") or 0))
        rt_detail = top.get("description")
    banking_items.append({
        "label": "Circular entry (mirror credit/debit)",
        "checked": bool(round_trips),
        "severity": "high" if round_trips else "positive",
        "detail": rt_detail,
    })

    # K5c. Counterparties present on both credit and debit sides.
    both_side_detail = None
    both_side_hit = False
    try:
        if customer_report:
            from data.loader import get_transactions_df
            from utils.narration_utils import extract_recipient_name
            tdf = get_transactions_df()
            cdf = tdf[tdf["cust_id"] == customer_report.meta.customer_id].copy()
            if not cdf.empty:
                cdf["_who"] = cdf["tran_partclr"].astype(str).map(
                    lambda s: (extract_recipient_name(s) or "").strip().upper()
                )
                cdf = cdf[cdf["_who"] != ""]
                cdf["_amt"] = cdf["tran_amt_in_ac"].astype(float).abs()
                cr = cdf[cdf["dr_cr_indctor"] == "C"]
                dr = cdf[cdf["dr_cr_indctor"] == "D"]
                shared = set(cr["_who"]).intersection(set(dr["_who"]))
                if shared:
                    both_side_hit = True
                    sub = cdf[cdf["_who"].isin(shared)]
                    top_party = (
                        sub.groupby("_who")["_amt"].sum().sort_values(ascending=False).index[0]
                    )
                    cr_amt = float(cr.loc[cr["_who"] == top_party, "_amt"].sum())
                    dr_amt = float(dr.loc[dr["_who"] == top_party, "_amt"].sum())
                    both_side_detail = (
                        f"{len(shared)} counterparties on both sides — top: "
                        f"{top_party[:40]} (₹{cr_amt:,.0f} in / ₹{dr_amt:,.0f} out)"
                    )
    except Exception:
        pass
    banking_items.append({
        "label": "Counterparties on both credit & debit",
        "checked": both_side_hit,
        "severity": "medium" if both_side_hit else "neutral",
        "detail": both_side_detail,
    })

    # K6. EMI obligations
    has_emis = customer_report and customer_report.emis and len(customer_report.emis) > 0
    emi_detail = None
    if has_emis:
        total_emi = sum(e.amount for e in customer_report.emis)
        emi_detail = f"₹{total_emi:,.0f} total across {len(customer_report.emis)} lender(s)"
    banking_items.append({
        "label": "EMI obligations present",
        "checked": bool(has_emis),
        "severity": "medium" if has_emis else "neutral",
        "detail": emi_detail,
    })

    # K7. NACH mandate / SPLN EMI (paired with EMI above)
    mandate_emis = _events_of_type("mandate_emi")
    banking_items.append({
        "label": "NACH mandate EMI detected",
        "checked": bool(mandate_emis),
        "severity": "medium" if mandate_emis else "neutral",
        "detail": mandate_emis[0].get("description") if mandate_emis else None,
    })

    # K8. Rent payments
    has_rent = customer_report and customer_report.rent is not None
    banking_items.append({
        "label": "Rent payments present",
        "checked": bool(has_rent),
        "severity": "neutral",
        "detail": f"₹{customer_report.rent.amount:,.0f} ({customer_report.rent.frequency} transactions)" if has_rent else None,
    })

    # K9. Credit card bill payments
    cc_payments = _events_of_type("cc_payment")
    banking_items.append({
        "label": "Credit card bill payments",
        "checked": bool(cc_payments),
        "severity": "positive" if cc_payments else "neutral",
        "detail": cc_payments[0].get("description") if cc_payments else None,
    })

    # K11. Land payments
    land_events = _events_of_type("land_payment")
    banking_items.append({
        "label": "Land purchase payments",
        "checked": bool(land_events),
        "severity": "medium" if land_events else "neutral",
        "detail": land_events[0].get("description") if land_events else None,
    })

    # K12. ATM withdrawals — trend and location
    atm_events = _events_of_type("atm_withdrawal")
    if atm_events:
        ev = atm_events[0]
        is_elevated = ev.get("_is_elevated", False)
        top = ev.get("_top_address")
        detail = ev.get("description", "")
        if top:
            detail += f" | Most frequent ATM: {top['address']} ({top['count']} times)"
        banking_items.append({
            "label": "ATM withdrawals elevated",
            "checked": is_elevated,
            "severity": "medium" if is_elevated else "neutral",
            "detail": detail,
        })
    else:
        banking_items.append({
            "label": "ATM withdrawals elevated",
            "checked": False,
            "severity": "neutral",
            "detail": None,
        })

    # K13–K15. Transaction-level checks (require raw DataFrame)
    try:
        from data.loader import get_transactions_df
        from utils.narration_utils import extract_recipient_name, clean_narration

        cust_id = customer_report.meta.customer_id if customer_report else None
        if cust_id is not None:
            df = get_transactions_df()
            cdf = df[df["cust_id"] == cust_id].copy()

            if not cdf.empty:
                narrations = cdf["tran_partclr"].fillna("")
                amounts = cdf["tran_amt_in_ac"].fillna(0).astype(float)
                directions = cdf["dr_cr_indctor"].fillna("")

                # --- K13. Credits / debits above 95th percentile ---------------
                outlier_parts = []
                for direction, label in [("C", "credit"), ("D", "debit")]:
                    mask = directions == direction
                    dir_amounts = amounts[mask]
                    if len(dir_amounts) < 5:
                        continue
                    p95 = np.percentile(dir_amounts, 95)
                    outliers = cdf[mask & (amounts > p95)]
                    for _, row in outliers.iterrows():
                        narr = str(row.get("tran_partclr", ""))
                        merchant = extract_recipient_name(narr) or clean_narration(narr) or "Unknown"
                        amt = float(row.get("tran_amt_in_ac", 0))
                        outlier_parts.append(f"{merchant}: ₹{amt:,.0f} ({label})")

                has_outliers = bool(outlier_parts)
                banking_items.append({
                    "label": "Transactions above 95th percentile",
                    "checked": has_outliers,
                    "severity": "medium" if has_outliers else "neutral",
                    "detail": "; ".join(outlier_parts[:5]) if has_outliers else None,
                })

                # --- K14. Automated (NACH / mandate) debit & credit count ------
                narr_upper = narrations.str.upper()
                auto_mask = narr_upper.str.contains("NACH|MANDATE", na=False, regex=True)
                auto_debits = int((auto_mask & (directions == "D")).sum())
                auto_credits = int((auto_mask & (directions == "C")).sum())
                auto_total = auto_debits + auto_credits
                banking_items.append({
                    "label": "Automated (NACH/mandate) transactions",
                    "checked": auto_total > 0,
                    "severity": "neutral",
                    "detail": f"{auto_total} total ({auto_debits} debits, {auto_credits} credits)" if auto_total > 0 else None,
                })

                # --- K15. Payment mode distribution shift -----------------
                import pandas as pd

                def _infer_mode(row):
                    """Infer payment mode from tran_type, falling back to narration."""
                    tt = row.get("tran_type")
                    if pd.notna(tt) and str(tt).strip():
                        return str(tt).strip()
                    nu = str(row.get("tran_partclr", "")).strip().upper()
                    if "UPI" in nu:
                        return "UPI"
                    if "NEFT" in nu:
                        return "NEFT"
                    if "IMPS" in nu:
                        return "IMPS"
                    if "RTGS" in nu:
                        return "RTGS"
                    if "NACH-" in nu:
                        return "NACH"
                    if "MB:RECEIVED" in nu or "MB:SENT" in nu:
                        return "Mobile Banking"
                    if "IFT-" in nu:
                        return "IFT"
                    if nu.startswith("IB:RECEIVED FROM") or "IB:FUND" in nu:
                        return "Internet Banking"
                    if nu.startswith("FUND TRF FROM") or nu.startswith("FT FROM") or nu.startswith("FUNDS TRF FROM"):
                        return "Funds Transfer"
                    if nu.startswith("ATL/") or nu.startswith("ATW/"):
                        return "ATM"
                    if nu.startswith("PG "):
                        return "Payment Gateway"
                    if nu.startswith("PCD/"):
                        return "Card Payment"
                    if nu.startswith("CLG TO "):
                        return "Cheque"
                    return "Other"

                _mode_col = cdf.apply(_infer_mode, axis=1)
                _dates = pd.to_datetime(cdf["tran_date"], format="%Y-%m-%d", errors="coerce")
                _periods = _dates.dt.to_period("M")
                _months_all = sorted(_periods.dropna().unique())

                if len(_months_all) >= T.MODE_SHIFT_MIN_MONTHS:
                    _recent_set = set(_months_all[-T.MODE_SHIFT_RECENT_MONTHS:])
                    _is_recent = _periods.map(
                        lambda m: m in _recent_set if pd.notna(m) else False
                    )

                    _earlier_mask = ~_is_recent & _periods.notna()
                    _recent_mask = _is_recent

                    if (int(_earlier_mask.sum()) >= T.MODE_SHIFT_MIN_TRANSACTIONS
                            and int(_recent_mask.sum()) >= T.MODE_SHIFT_MIN_TRANSACTIONS):

                        _e_dist = _mode_col[_earlier_mask].value_counts(normalize=True) * 100
                        _r_dist = _mode_col[_recent_mask].value_counts(normalize=True) * 100

                        _all_modes = sorted(set(_e_dist.index) | set(_r_dist.index))
                        _shifts = {}
                        for _m in _all_modes:
                            _old = _e_dist.get(_m, 0.0)
                            _new = _r_dist.get(_m, 0.0)
                            _delta = _new - _old
                            if abs(_delta) >= T.MODE_SHIFT_THRESHOLD_PP:
                                _shifts[_m] = (_old, _new, _delta)

                        if _shifts:
                            _parts = []
                            for _m, (_old, _new, _delta) in sorted(
                                _shifts.items(), key=lambda x: -abs(x[1][2])
                            ):
                                _sign = "+" if _delta > 0 else ""
                                _parts.append(
                                    f"{_m}: {_old:.0f}% \u2192 {_new:.0f}% ({_sign}{_delta:.0f}pp)"
                                )
                            banking_items.append({
                                "label": "Payment mode distribution shift",
                                "checked": True,
                                "severity": "medium",
                                "detail": "; ".join(_parts),
                            })
                        else:
                            banking_items.append({
                                "label": "Payment mode distribution shift",
                                "checked": False,
                                "severity": "neutral",
                                "detail": None,
                            })
                    else:
                        banking_items.append({
                            "label": "Payment mode distribution shift",
                            "checked": False,
                            "severity": "neutral",
                            "detail": None,
                        })
                else:
                    banking_items.append({
                        "label": "Payment mode distribution shift",
                        "checked": False,
                        "severity": "neutral",
                        "detail": None,
                    })
    except Exception:
        pass  # fail-soft: skip transaction-level checks if data unavailable

    # K16. Emerging merchants (new in recent months, absent before)
    if customer_report and customer_report.merchant_features:
        em = customer_report.merchant_features.get("emerging_merchants", {})
        em_list = em.get("emerging_merchants", [])
        if em_list:
            names = ", ".join(e["name"] for e in em_list[:3])
            detail = f"{len(em_list)} new: {names}"
            banking_items.append({"label": "Emerging merchants detected", "checked": True,
                                   "severity": "medium", "detail": detail})

    return {"banking": banking_items}
