"""Report summary chain - LLM-based customer review generation.

Generates the executive summary (3-4 lines, financial-metrics focus) for the
banking report. Uses LangChain Expression Language (LCEL) with Ollama models.
"""

import logging
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate

from utils.llm_factory import create_chat_model

from schemas.customer_report import CustomerReport
from utils.helpers import mask_customer_id
from config.settings import SUMMARY_MODEL, is_thinking_model
import time as _time
from utils.llm_utils import extract_reasoning, log_token_usage
from config.prompts import CUSTOMER_REVIEW_PROMPT

logger = logging.getLogger(__name__)

# Default model for summary generation — dedicated reasoning model
_SUMMARY_MODEL = SUMMARY_MODEL


def create_summary_chain(model_name: str = _SUMMARY_MODEL):
    """
    Create an LCEL chain for generating customer reviews.

    Args:
        model_name: Ollama model to use (default: llama3.1:8b)

    Returns:
        LCEL chain that takes {customer_id, data_summary} and returns AIMessage
    """
    prompt = ChatPromptTemplate.from_template(CUSTOMER_REVIEW_PROMPT)
    reasoning = True if is_thinking_model(model_name) else None
    llm = create_chat_model(model_name, reasoning=reasoning)

    return prompt | llm


def generate_customer_review(
    report: CustomerReport,
    rg_salary_data: dict = None,
    model_name: str = _SUMMARY_MODEL,
) -> Optional[str]:
    """
    Generate an LLM-based customer review from populated report sections.

    This function:
    1. Extracts only populated sections from the report
    2. Builds a data summary string
    3. Invokes the LLM chain
    4. Returns the generated review (or None on failure)

    Args:
        report: CustomerReport with populated sections
        rg_salary_data: Optional RG salary algorithm output — used to prefer
                        the authoritative salary amount over banking detection.
        model_name: Ollama model to use

    Returns:
        Generated review string, or None if generation fails
    """
    # Build data summary from populated sections only
    sections = _build_data_summary(report, rg_salary_data=rg_salary_data)

    if not sections:
        return None

    data_summary = "\n".join(sections)

    try:
        chain = create_summary_chain(model_name)
        t0 = _time.time()
        raw = chain.invoke({
            "customer_id": mask_customer_id(report.meta.customer_id),
            "data_summary": data_summary,
        })
        log_token_usage(raw, label="CustomerReview",
                        customer_id=report.meta.customer_id,
                        wall_time_s=_time.time() - t0)
        review = extract_reasoning(raw, label="CustomerReview", customer_id=report.meta.customer_id)
        return review.strip() if review else None
    except Exception as e:
        logger.warning("Customer review generation failed: %s", e)
        return None


def _build_data_summary(report: CustomerReport, rg_salary_data: dict = None) -> list:
    """
    Build data summary lines from populated report sections.

    Only includes sections that have data - never mentions
    missing sections.

    Args:
        report: CustomerReport to summarize
        rg_salary_data: Optional RG salary algorithm output dict.

    Returns:
        List of summary strings for each populated section
    """
    sections = []

    # Resolve authoritative salary — rg_sal first (same priority as scorecard)
    _rg_sal = (rg_salary_data or {}).get("rg_sal") if rg_salary_data else None
    _auth_salary_amt = (
        (_rg_sal.get("salary_amount") if _rg_sal else None)
        or (report.salary.avg_amount if report.salary else None)
    )
    _auth_salary_merchant = (
        (_rg_sal.get("merchant") if _rg_sal else None)
        or (report.salary.narration.split()[0].title() if report.salary and report.salary.narration else None)
    )

    # Category spending (exclude "Others" — merchant analysis covers those)
    if report.category_overview:
        filtered_cats = {k: v for k, v in report.category_overview.items()
                         if k.strip().lower() not in ("other", "others")}
        top_cats = sorted(
            filtered_cats.items(),
            key=lambda x: x[1],
            reverse=True
        )[:3]
        if top_cats:
            cats_str = ", ".join(f"{k}: {v:,.0f}" for k, v in top_cats)
            sections.append(f"Top spending categories: {cats_str}")

    # Monthly cashflow
    if report.monthly_cashflow:
        total_inflow = sum(m.get('inflow', 0) for m in report.monthly_cashflow)
        total_outflow = sum(m.get('outflow', 0) for m in report.monthly_cashflow)
        avg_net = (total_inflow - total_outflow) / max(1, len(report.monthly_cashflow))
        sections.append(
            f"Monthly cashflow: Avg net {avg_net:,.0f} INR "
            f"(Total in: {total_inflow:,.0f}, out: {total_outflow:,.0f})"
        )

    # Salary — use authoritative amount (rg_sal preferred, same as scorecard)
    if _auth_salary_amt:
        merchant_str = f" from {_auth_salary_merchant}" if _auth_salary_merchant else ""
        # Use rg_sal count when rg_sal provides the amount; fall back to banking detection
        if _rg_sal and _rg_sal.get("salary_amount"):
            _rg_sal_count = _rg_sal.get("transaction_count")
            freq_str = f" ({_rg_sal_count} months)" if _rg_sal_count else ""
        elif report.salary:
            freq_str = f" ({report.salary.frequency} transactions)"
        else:
            freq_str = ""
        sections.append(
            f"Salary income: {_auth_salary_amt:,.0f} INR average{merchant_str}{freq_str}"
        )

    # EMIs — EMIBlock.amount is per-transaction average, not total
    if report.emis:
        avg_emi = sum(e.amount for e in report.emis)
        emi_count = sum(e.frequency for e in report.emis)
        sections.append(f"EMI commitments: {avg_emi:,.0f} INR average per payment ({emi_count} debit transactions)")

    # Rent
    if report.rent:
        sections.append(
            f"Rent payments: {report.rent.amount:,.0f} INR "
            f"({report.rent.frequency} transactions)"
        )

    # Banking FOIR (computed from available EMI + rent / salary)
    # Note: e.amount and rent.amount are per-transaction averages, which approximates monthly obligation
    if _auth_salary_amt and _auth_salary_amt > 0:
        _emi_avg = sum(e.amount for e in report.emis) if report.emis else 0
        _rent_amt = report.rent.amount if report.rent else 0
        _foir = (_emi_avg + _rent_amt) / _auth_salary_amt * 100
        _tag = " [OVER-LEVERAGED]" if _foir > 65 else (" [STRETCHED]" if _foir > 40 else " [COMFORTABLE]")
        sections.append(f"Banking FOIR (EMI+Rent/Salary): {_foir:.1f}%{_tag}")

    # Bills
    if report.bills:
        total_bills = sum(b.avg_amount * b.frequency for b in report.bills)
        sections.append(f"Utility bills: {total_bills:,.0f} INR total")

    # Top merchants (kept in financial overview for basic context)
    if report.top_merchants:
        top_merchant = report.top_merchants[0]
        sections.append(
            f"Most frequent merchant: {top_merchant.get('name', 'Unknown')} "
            f"({top_merchant.get('count', 0)} transactions, "
            f"{top_merchant.get('total', 0):,.0f} INR)"
        )

    # Merchant behavioral summary — compact block for dedicated paragraph
    if report.merchant_features:
        mf = report.merchant_features
        m_parts = []

        regular = mf.get("regular_merchants", [])
        if regular:
            names = ", ".join(r["merchant"] for r in regular[:3])
            m_parts.append(f"Regular merchants: {len(regular)} ({names})")

        anomalies = mf.get("anomaly_merchants", [])
        if anomalies:
            a = anomalies[0]
            m_parts.append(f"Anomaly: {a['merchant']} INR {a['amount']:,.0f}")

        concentration = mf.get("concentration", {})
        if concentration.get("total_merchants", 0) > 0:
            m_parts.append(
                f"Concentration: top-1 = {concentration['top_1_pct']:.0f}%, "
                f"{concentration['total_merchants']} merchants total"
            )

        # Favourite merchants with IPT (debit + credit separately)
        favourites = mf.get("favourite_merchants_ipt", {})
        for dir_label, dir_key in [("Favourite debit merchants", "debit"),
                                    ("Favourite credit merchants", "credit")]:
            fav_list = favourites.get(dir_key, [])
            if fav_list:
                fav_parts = []
                for f in fav_list:
                    ipt_str = (f", avg {f['avg_ipt_days']:.0f} days apart"
                               if f.get("avg_ipt_days") else "")
                    fav_parts.append(
                        f"{f['merchant']} ({f['count']} txns, "
                        f"INR {f['total_amount']:,.0f}{ipt_str})"
                    )
                m_parts.append(f"{dir_label}: " + "; ".join(fav_parts))

        # Significant counterparties (>= 25% of total flow)
        significant = mf.get("significant_merchants", [])
        if significant:
            sig_parts = []
            for s in significant:
                pcts = []
                if s["debit_pct"] >= 0.25:
                    pcts.append(f"{s['debit_pct']:.0%} of debits")
                if s["credit_pct"] >= 0.25:
                    pcts.append(f"{s['credit_pct']:.0%} of credits")
                if pcts:
                    sig_parts.append(
                        f"{s['merchant']} accounts for {' and '.join(pcts)}"
                    )
            if sig_parts:
                m_parts.append("Significant counterparties: " + "; ".join(sig_parts))

        # Two-way merchants (both credits and debits, excluding self-transfers)
        bidir = mf.get("bidirectional_merchants", [])
        if bidir:
            bi_parts = []
            for b in bidir[:3]:
                label = "net inflow" if b["net_flow"] >= 0 else "net outflow"
                pattern = b.get("flow_pattern", "")
                pattern_str = ""
                if pattern == "received_then_paid":
                    pattern_str = ", received first then paid"
                elif pattern == "paid_then_received":
                    pattern_str = ", paid first then received back"
                date_str = ""
                if b.get("first_credit") and b.get("first_debit"):
                    date_str = (f", credits {b['first_credit']} to {b['last_credit']}"
                                f", debits {b['first_debit']} to {b['last_debit']}")
                bi_parts.append(
                    f"{b['merchant']} (credit INR {b['total_credit']:,.0f}, "
                    f"debit INR {b['total_debit']:,.0f}, "
                    f"{label} INR {abs(b['net_flow']):,.0f}"
                    f"{pattern_str}{date_str})"
                )
            m_parts.append("Two-way merchants: " + "; ".join(bi_parts))

        emerging = mf.get("emerging_merchants", {})
        em_list = emerging.get("emerging_merchants", [])
        if em_list:
            names = ", ".join(e["name"] for e in em_list[:5])
            m_parts.append(f"Emerging merchants (new in recent 3 months, absent before): {len(em_list)} — {names}")

        if m_parts:
            sections.append("MERCHANT PROFILE: " + ". ".join(m_parts))

    # Account quality observations — presented as plain facts, no score label
    if report.account_quality:
        obs = report.account_quality.get("observations", [])
        for ob in obs:
            sections.append(ob)

    # Detected transaction events (PF withdrawal, post-salary routing, etc.)
    if report.events:
        from tools.event_detector import format_events_for_prompt
        events_block = format_events_for_prompt(report.events)
        if events_block:
            sections.append(events_block)

    # Explicitly list absent data types so the LLM does not invent them
    absent = []
    if not _auth_salary_amt:
        absent.append("salary income")
    if not report.emis:
        absent.append("EMI / loan repayments")
    if not report.rent:
        absent.append("rent payments")
    if not report.bills:
        absent.append("utility bills")
    if not report.category_overview:
        absent.append("spending categories")
    if not report.monthly_cashflow:
        absent.append("monthly cashflow")
    if not report.merchant_features:
        absent.append("merchant profile")
    if not report.events:
        absent.append("transaction events")
    if absent:
        sections.append(
            "DATA NOT AVAILABLE (do NOT invent or assume these): "
            + ", ".join(absent)
        )

    return sections

