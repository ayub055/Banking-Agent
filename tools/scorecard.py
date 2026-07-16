"""Scorecard computation — one-page risk verdict for the banking report.

Computes a structured scorecard dict from a CustomerReport.
No LLM calls — pure deterministic threshold logic.

The scorecard contains (only the keys the bank_v2 view model consumes):
  - verdict:     "LOW RISK" / "CAUTION" / "HIGH RISK"
  - verdict_rag: "green" / "amber" / "red"
  - concerns:    up to 3 risk findings
  - verify:      items to cross-check
"""

import logging
from utils.helpers import format_inr

logger = logging.getLogger(__name__)


def _banking_signals(customer_report, rg_salary_data: dict = None) -> list:
    """Compute banking risk signals from CustomerReport."""
    signals = []
    rg_salary_data = rg_salary_data or {}

    # Income — hierarchy: rg_sal → rg_income → report.salary avg
    rg_sal = rg_salary_data.get("rg_sal")
    rg_income = rg_salary_data.get("rg_income")

    if rg_sal:
        amt = rg_sal.get("salary_amount") or 0
        n = rg_sal.get("transaction_count") or 1
        merchant = rg_sal.get("merchant", "")
        rag = "green" if n >= 3 else "amber"
        note = merchant if merchant else ("Consistent" if rag == "green" else "Irregular")
        signals.append({
            "label": "Income",
            "value": f"INR {format_inr(amt)} /mo",
            "rag": rag,
            "note": f"RG SAL · {note}",
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
        })
    else:
        signals.append({"label": "Income", "value": "Not detected", "rag": "red", "note": "No salary found"})

    # Red Flag Spending
    betting = 0.0
    if customer_report.category_overview:
        for key in ("Digital_Betting_Gaming", "Betting_Gaming", "Betting", "Gaming"):
            if key in customer_report.category_overview:
                betting = customer_report.category_overview[key]
                break
    if betting > 0:
        rag = "red" if betting >= 500 else "amber"
        signals.append({
            "label": "Transaction Red flag",
            "value": f"INR {format_inr(betting)}",
            "rag": rag,
            "note": "Betting/Gaming detected",
        })
    else:
        signals.append({"label": "Transaction Red flag", "value": "None", "rag": "green", "note": "No flag categories"})

    # Account Type (from account_quality)
    if customer_report.account_quality:
        aq           = customer_report.account_quality
        account_type = aq.get("account_type", "unknown")
        score        = aq.get("primary_score", 50)
        rag_map      = {"primary": "green", "secondary": "amber", "conduit": "red", "unknown": "neutral"}
        rag          = rag_map.get(account_type, "neutral")
        signals.append({
            "label": "Account Type",
            "value": account_type.title(),
            "rag":   rag,
            "note":  f"Score {score}/100",
        })

    return signals


def _derive_concerns_verify(signals: list) -> tuple:
    """Derive concerns and verify items from the banking signal list."""
    concerns = []
    for s in signals:
        if s["rag"] == "red" and len(concerns) < 3:
            concerns.append(f"{s['label']}: {s['value']} ({s['note']})")

    verify = ["Confirm income source from employer or IT returns"]
    return concerns[:3], verify[:3]


def compute_scorecard(customer_report=None, rg_salary_data: dict = None) -> dict:
    """Compute a structured risk scorecard from the banking report data.

    Returns:
        dict with keys: verdict, verdict_rag, concerns, verify
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

    concerns, verify = _derive_concerns_verify(signals)

    return {
        "verdict": verdict,
        "verdict_rag": verdict_rag,
        "concerns": concerns,
        "verify": verify,
    }
