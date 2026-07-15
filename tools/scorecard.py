"""Scorecard computation — one-page risk verdict for the banking report.

Computes a structured scorecard dict from a CustomerReport.
No LLM calls — pure deterministic threshold logic using config/thresholds.py.

The scorecard contains:
  - verdict:     "LOW RISK" / "CAUTION" / "HIGH RISK"
  - verdict_rag: "green" / "amber" / "red"
  - signals:     list of RAG-tagged metric chips
  - strengths:   up to 3 positive findings
  - concerns:    up to 3 risk findings
  - verify:      items to cross-check
  - narrative:   LLM summary text (injected by caller)
"""

import logging
from typing import Optional
from utils.helpers import format_inr

import config.thresholds as T

logger = logging.getLogger(__name__)


def _rag(value, green_max=None, amber_max=None, green_min=None, amber_min=None,
         invert=False):
    """Return 'green' / 'amber' / 'red' for a numeric value.

    Two modes:
      Lower-is-better (invert=False, default):
        value <= green_max → green, <= amber_max → amber, else red
      Higher-is-better (invert=True):
        value >= green_min → green, >= amber_min → amber, else red
    """
    if value is None:
        return "neutral"
    if not invert:
        if green_max is not None and value <= green_max:
            return "green"
        if amber_max is not None and value <= amber_max:
            return "amber"
        return "red"
    else:
        if green_min is not None and value >= green_min:
            return "green"
        if amber_min is not None and value >= amber_min:
            return "amber"
        return "red"


def _banking_signals(customer_report, rg_salary_data: dict = None, affluence_amt=None, income_source=None) -> list:
    """Compute banking risk signals from CustomerReport."""
    signals = []
    rg_salary_data = rg_salary_data or {}

    # 7. Income — hierarchy: affluence_amt → rg_sal → rg_income → report.salary avg
    rg_sal = rg_salary_data.get("rg_sal")
    rg_income = rg_salary_data.get("rg_income")

    # Build cross-reference lines for tooltip
    def _xref_lines():
        parts = []
        if rg_sal:
            amt = rg_sal.get("salary_amount") or 0
            m = rg_sal.get("merchant", "")
            parts.append(f"RG SAL: INR {amt:,.0f}/mo" + (f" · {m}" if m else ""))
        if rg_income:
            parts.append(f"RG Income (multi-source): INR {rg_income['total_income']:,.0f}/mo ({rg_income['source_count']} sources)")
        if customer_report.salary:
            parts.append(f"Txn avg: INR {customer_report.salary.avg_amount:,.0f} ({customer_report.salary.frequency} occurrences)")
        return parts

    if affluence_amt:
        xref = _xref_lines()
        tooltip = f"Source: Relationship Profile (Affluence Amount).\nINR {affluence_amt:,.0f} — 6-month income estimate from bureau relationship profiles."
        if xref:
            tooltip += "\n\nCross-references:\n" + "\n".join(xref)
        signals.append({
            "label": "Income",
            "value": f"INR {format_inr(affluence_amt)}",
            "rag": "green",
            "note": income_source if income_source else "Relationship profile",
            "tooltip": tooltip,
        })
    elif rg_sal:
        amt = rg_sal.get("salary_amount") or 0
        n = rg_sal.get("transaction_count") or 1
        merchant = rg_sal.get("merchant", "")
        rag = "green" if n >= 3 else "amber"
        note = merchant if merchant else ("Consistent" if rag == "green" else "Irregular")
        tooltip = (
            f"Source: RG SAL (internal salary algorithm).\n"
            f"Amount: INR {amt:,.0f}/mo · Transactions: {n}"
            + (f" · Employer: {merchant}" if merchant else "")
        )
        signals.append({
            "label": "Income",
            "value": f"INR {format_inr(amt)} /mo",
            "rag": rag,
            "note": f"RG SAL · {note}",
            "tooltip": tooltip,
        })
    elif rg_income:
        total = rg_income.get("total_income") or 0
        n_src = rg_income.get("source_count") or 1
        rag = "green" if n_src >= 2 else "amber"
        signals.append({
            "label": "Income",
            "value": f"INR {format_inr(total)} /mo",
            "rag": rag,
            "note": f"RG Income · {n_src} source{'s' if n_src != 1 else ''}",
            "tooltip": (
                f"Source: RG Income (multi-source total).\n"
                f"Total: INR {total:,.0f}/mo across {n_src} contributing source{'s' if n_src != 1 else ''}.\n"
                + (rg_income.get("observation", "") or "")
            ),
        })
    elif customer_report.salary:
        avg = customer_report.salary.avg_amount
        freq = customer_report.salary.frequency
        rag = "green" if freq >= 3 else "amber"
        signals.append({
            "label": "Income",
            "value": f"INR {format_inr(avg)} avg",
            "rag": rag,
            "note": f"Txn avg · {'Consistent' if rag == 'green' else 'Irregular'}",
            "tooltip": (
                f"Source: banking transaction salary detection.\n"
                f"Avg amount: INR {avg:,.0f} · Occurrences: {freq}"
            ),
        })
    else:
        signals.append({"label": "Income", "value": "Not detected", "rag": "red", "note": "No salary found",
                        "tooltip": "No salary transactions detected in the analysis period."})

    # 9. Red Flag Spending
    betting = 0.0
    if customer_report.category_overview:
        for key in ("Digital_Betting_Gaming", "Betting_Gaming", "Betting", "Gaming"):
            if key in customer_report.category_overview:
                betting = customer_report.category_overview[key]
                break
    if betting > 0:
        rag = "red" if betting >= 500 else "amber"
        tooltip = (
            f"Betting / Gaming spend detected: INR {betting:,.0f}.\n"
            f"Thresholds: < INR 500 = amber · ≥ INR 500 = red flag"
        )
        signals.append({
            "label": "Transaction Red flag",
            "value": f"INR {format_inr(betting)}",
            "rag": rag,
            "note": "Betting/Gaming detected",
            "tooltip": tooltip,
        })
    else:
        signals.append({"label": "Transaction Red flag", "value": "None", "rag": "green", "note": "No flag categories",
                        "tooltip": "No betting, gaming, or flagged transaction categories detected."})

    # 10. Account Type (from account_quality)
    if customer_report.account_quality:
        aq           = customer_report.account_quality
        account_type = aq.get("account_type", "unknown")
        score        = aq.get("primary_score", 50)
        rag_map      = {"primary": "green", "secondary": "amber", "conduit": "red", "unknown": "neutral"}
        rag          = rag_map.get(account_type, "neutral")
        tooltip = (
            f"Account classification: {account_type.title()} · Confidence score: {score}/100.\n"
            f"Primary = main salary/income account · Secondary = supplementary · Conduit = pass-through"
        )
        signals.append({
            "label": "Account Type",
            "value": account_type.title(),
            "rag":   rag,
            "note":  f"Score {score}/100",
            "tooltip": tooltip,
        })

    return signals


def _derive_strengths_concerns(signals: list) -> tuple:
    """Derive strengths, concerns, verify from the banking signal list."""
    strengths, concerns, verify = [], [], []

    for s in signals:
        if s["rag"] == "red" and len(concerns) < 3:
            concerns.append(f"{s['label']}: {s['value']} ({s['note']})")
    for s in signals:
        if s["rag"] == "green" and len(strengths) < 3:
            strengths.append(f"{s['label']}: {s['value']}")

    # Verify items — FOIR signal
    for s in signals:
        if s["label"] == "FOIR" and s["rag"] in ("amber", "red"):
            verify.append("Cross-verify declared income vs salary deposits")
            break

    if not verify:
        verify.append("Confirm income source from employer or IT returns")

    return strengths[:3], concerns[:3], verify[:3]


def compute_scorecard(customer_report=None, rg_salary_data: dict = None) -> dict:
    """Compute a structured risk scorecard from the banking report data.

    Returns:
        dict with keys: verdict, verdict_rag, signals, strengths, concerns, verify, narrative
    """
    signals = []
    try:
        if customer_report:
            signals.extend(_banking_signals(customer_report, rg_salary_data=rg_salary_data))
    except Exception as e:
        logger.warning("Banking signal computation failed: %s", e)

    # Verdict from RED count
    red_count = sum(1 for s in signals if s["rag"] == "red")
    if red_count >= 3:
        verdict, verdict_rag = "HIGH RISK", "red"
    elif red_count >= 1:
        verdict, verdict_rag = "CAUTION", "amber"
    else:
        verdict, verdict_rag = "LOW RISK", "green"

    strengths, concerns, verify = _derive_strengths_concerns(signals)

    return {
        "verdict": verdict,
        "verdict_rag": verdict_rag,
        "signals": signals,
        "strengths": strengths,
        "concerns": concerns,
        "verify": verify,
        "narrative": "",
    }
