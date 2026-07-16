"""View-model assembly for templates/bank_report_v2.html.

Pure, deterministic, fail-soft. Consumes an already-built CustomerReport plus
the deterministic scorecard and (optional) rg_salary_data, and emits a flat
dict whose keys map 1:1 to sections of bank_report_v2.html.

No I/O, no LLM. Every derivation is wrapped — on any error the corresponding
sub-context becomes None so the template hides the section instead of crashing.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from schemas.customer_report import CustomerReport
from utils.helpers import safe_call as _safe

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------

def build_bank_v2_context(
    customer_report: Optional[CustomerReport],
    scorecard: Optional[dict],
    rg_salary_data: Optional[dict] = None,
    combined_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the v2 banking-report Jinja context.

    Returns a dict with one key per template section. Sections that cannot be
    derived are set to ``None`` so the template can hide them.
    """
    ctx: Dict[str, Any] = {
        "header": None,
        "verdict": None,
        "kpis": None,
        "checks": None,
        "summary": None,
        "cashflow": None,
        "heatmap": None,
        "category_mix": None,
        "analysis": None,
        "recurring_debits": None,
        "recurring_credits": None,
        "loans": None,
        "loan_disbursals": None,
        "credit_remitters": None,
        "debit_beneficiaries": None,
        "transactions": None,
    }

    if customer_report is None:
        return ctx

    cust_df = _safe(_load_cust_df, customer_report.meta.customer_id)
    balance_info = _safe(_load_balance_info, customer_report.meta.customer_id)

    ctx["header"] = _safe(_build_header, customer_report)
    ctx["verdict"] = _safe(_build_verdict, scorecard)
    ctx["kpis"] = _safe(_build_kpis, customer_report, scorecard, balance_info)
    ctx["checks"] = _safe(_build_checks, customer_report, scorecard)
    ctx["summary"] = _safe(_build_summary, customer_report, combined_summary, scorecard)
    ctx["cashflow"] = _safe(_build_cashflow, customer_report, balance_info, cust_df)
    ctx["heatmap"] = _safe(_build_heatmap, cust_df)
    ctx["category_mix"] = _safe(_build_category_mix, customer_report, cust_df)
    ctx["analysis"] = _safe(_build_analysis, customer_report, cust_df)
    ctx["recurring_debits"] = _safe(_build_recurring_debits, customer_report, cust_df)
    ctx["recurring_credits"] = _safe(_build_recurring_credits, customer_report, rg_salary_data, cust_df)
    ctx["loans"] = _safe(_build_loan_cards, customer_report)
    ctx["loan_disbursals"] = _safe(_build_loan_disbursals, customer_report)
    ctx["credit_remitters"] = _safe(_build_credit_remitters, customer_report)
    ctx["debit_beneficiaries"] = _safe(_build_debit_beneficiaries, customer_report)
    ctx["transactions"] = _safe(_build_transactions, customer_report, cust_df)

    return ctx


# ---------------------------------------------------------------------------
# Internal helpers — fail-soft wrapper (_safe) is the shared utils.helpers.safe_call
# ---------------------------------------------------------------------------

def _load_cust_df(customer_id: int) -> Optional[pd.DataFrame]:
    from data.loader import get_transactions_df
    df = get_transactions_df()
    cust = df[df["cust_id"] == customer_id].copy()
    if cust.empty:
        return None
    cust["tran_date"] = pd.to_datetime(cust["tran_date"], errors="coerce")
    cust = cust.dropna(subset=["tran_date"])
    return cust if not cust.empty else None


def _load_balance_info(customer_id: int) -> Optional[dict]:
    from tools.analytics import get_balance_trend
    return get_balance_trend(customer_id)


# ---------------------------------------------------------------------------
# Header / verdict
# ---------------------------------------------------------------------------

def _build_header(report: CustomerReport) -> dict:
    m = report.meta
    return {
        "customer_id": m.customer_id,
        "name": m.prty_name or "",
        "period": m.analysis_period,
        "txn_count": m.transaction_count,
    }


def _build_verdict(scorecard: Optional[dict]) -> dict:
    if not scorecard:
        return {"label": "REVIEW", "rag": "amber"}
    return {
        "label": scorecard.get("verdict", "REVIEW"),
        "rag": scorecard.get("verdict_rag", "amber"),
    }


# ---------------------------------------------------------------------------
# KPI strip
# ---------------------------------------------------------------------------

def _build_kpis(
    report: CustomerReport,
    scorecard: Optional[dict],
    balance_info: Optional[dict],
) -> dict:
    return {
        "salary": _kpi_salary(report),
        "balance": _kpi_balance(report, balance_info),
        "net_cashflow": _kpi_net_cashflow(report),
        "emi": _kpi_emi(report),
        "stress": _kpi_stress(scorecard),
    }


def _kpi_salary(report: CustomerReport) -> Optional[dict]:
    sal = report.salary
    if not sal:
        return None
    months = _months_from_cashflow(report)
    n_total = len(months) or 6
    n_present = min(sal.frequency, n_total)
    strip = []
    for i, label in enumerate(months or _fallback_months(n_total)):
        if i < n_present:
            status = "ok"
        else:
            status = "miss"
        try:
            dt = pd.to_datetime(label + "-01")
            bubble = dt.strftime("%b")[0]
            title = dt.strftime("%b '%y")
        except Exception:
            bubble = label[:1]
            title = label
        strip.append({"label": bubble, "title": title, "status": status})
    return {
        "avg_amount": round(sal.avg_amount, 0),
        "frequency": sal.frequency,
        "months_total": n_total,
        "strip": strip,
        "rag": "green" if sal.frequency >= max(3, n_total - 1) else "amber",
    }


def _kpi_balance(report: CustomerReport, balance_info: Optional[dict]) -> Optional[dict]:
    if not balance_info:
        return None
    monthly = balance_info.get("monthly_balances") or {}
    if not monthly:
        return None
    series = [float(v) for _, v in sorted(monthly.items())]
    avg = sum(series) / len(series) if series else 0
    return {
        "avg": round(avg, 0),
        "min": round(balance_info.get("min_balance", 0), 0),
        "max": round(balance_info.get("max_balance", 0), 0),
        "series": series,
        "labels": [k for k, _ in sorted(monthly.items())],
    }


def _kpi_net_cashflow(report: CustomerReport) -> Optional[dict]:
    cf = report.monthly_cashflow
    if not cf:
        return None
    nets = [float(m.get("net", 0)) for m in cf]
    total = sum(nets)
    negatives = [m["month"] for m in cf if float(m.get("net", 0)) < 0]
    return {
        "total": round(total, 0),
        "series": nets,
        "labels": [m["month"] for m in cf],
        "negative_months": negatives,
        "rag": "green" if total >= 0 else ("amber" if total > -50000 else "red"),
    }


def _kpi_emi(report: CustomerReport) -> Optional[dict]:
    emis = report.emis or []
    if not emis:
        return None
    total = sum(float(e.amount) for e in emis)
    pct_salary = None
    rag = "green"
    if report.salary and report.salary.avg_amount > 0:
        pct_salary = round(total / report.salary.avg_amount * 100, 1)
        if pct_salary >= 50:
            rag = "red"
        elif pct_salary >= 40:
            rag = "amber"
    return {
        "total": round(total, 0),
        "pct_salary": pct_salary,
        "count": len(emis),
        "rag": rag,
    }


def _kpi_stress(scorecard: Optional[dict]) -> dict:
    concerns = (scorecard or {}).get("concerns") or []
    verify = (scorecard or {}).get("verify") or []
    score = max(0, min(100, 100 - 20 * len(concerns) - 5 * len(verify)))
    rag = (scorecard or {}).get("verdict_rag") or "amber"
    label = {"green": "LOW", "amber": "MODERATE", "red": "HIGH"}.get(rag, "MODERATE")
    drivers: List[str] = []
    drivers.extend(concerns[:2])
    if not drivers and verify:
        drivers.extend(verify[:2])
    return {
        "score": score,
        "label": label,
        "rag": rag,
        "drivers": drivers,
    }


# ---------------------------------------------------------------------------
# Risk / FCU / Fraud check grid
# ---------------------------------------------------------------------------

def _build_checks(report: CustomerReport, scorecard: Optional[dict]) -> Optional[dict]:
    """Build Risk/FCU/Fraud check columns from the deterministic banking
    checklist already computed at build time (``CustomerReport.checklist``),
    so the view model stays a pure consumer.
    """
    cl = report.checklist or {}
    items = cl.get("banking") or []
    if not items:
        return None

    # Bucket each label into Risk / FCU / Fraud. Anything not listed defaults
    # to Risk. Labels must match checklist_builder's emitted labels exactly.
    FCU_LABELS = {
        "Post-disbursement fund diversion",
        "Big debits after salary credit (≥40% within 3d)",
        "Counterparties on both credit & debit",
        "Land purchase payments",
        "Automated (NACH/mandate) transactions",
        "Payment mode distribution shift",
    }
    FRAUD_LABELS = {
        "ATM withdrawals elevated",
        "Transactions above 95th percentile",
        "Circular entry (mirror credit/debit)",
    }

    def _to_check(it: dict) -> dict:
        sev = (it.get("severity") or "neutral").lower()
        checked = bool(it.get("checked"))
        # severity → color used by template's .check.{class}
        if sev in ("high", "red"):
            color = "red"
        elif sev in ("medium", "amber"):
            color = "amber"
        elif sev in ("positive", "green") or (checked and sev == "positive"):
            color = "green"
        else:
            color = "neutral"
        return {
            "label": it.get("label") or "—",
            "detail": (it.get("detail") or "").strip() or None,
            "color": color,
            "checked": checked,
        }

    risk: List[dict] = []
    fcu: List[dict] = []
    fraud: List[dict] = []
    for it in items:
        chk = _to_check(it)
        lbl = chk["label"]
        if lbl in FCU_LABELS:
            fcu.append(chk)
        elif lbl in FRAUD_LABELS:
            fraud.append(chk)
        else:
            risk.append(chk)

    if not (risk or fcu or fraud):
        return None

    all_checks = risk + fcu + fraud
    red_count = sum(1 for c in all_checks if c["color"] == "red")
    amber_count = sum(1 for c in all_checks if c["color"] == "amber")
    return {
        "risk": risk,
        "fcu": fcu,
        "fraud": fraud,
        "summary": f"{len(all_checks)} checks · {red_count} red · {amber_count} amber",
    }


# ---------------------------------------------------------------------------
# Banking summary
# ---------------------------------------------------------------------------

def _build_summary(
    report: CustomerReport,
    combined_summary: Optional[str],
    scorecard: Optional[dict],
) -> dict:
    """Banking summary. Assumes the LLM-generated narrative is always passed
    in (the report-generation path always produces one before rendering).
    """
    text = (combined_summary or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    teaser = " ".join(sentences[:2]).strip()
    rest = " ".join(sentences[2:]).strip()
    return {
        "teaser": teaser,
        "rest": rest,
        "has_more": bool(rest),
    }


# ---------------------------------------------------------------------------
# Cashflow / heatmap / category mix
# ---------------------------------------------------------------------------

def _build_cashflow(
    report: CustomerReport,
    balance_info: Optional[dict],
    cust_df: Optional[pd.DataFrame] = None,
) -> Optional[dict]:
    cf = report.monthly_cashflow
    if not cf:
        return None
    months = [m["month"] for m in cf]
    credits = [round(float(m.get("inflow", 0)), 0) for m in cf]
    debits = [-round(float(m.get("outflow", 0)), 0) for m in cf]
    # Per-transaction trend series (extremes + medians). None for months with
    # no transactions on that side — Chart.js will skip via spanGaps:false.
    max_credits, max_debits, median_credits, median_debits = _per_txn_trends(cust_df, months)

    balances: List[Optional[float]] = []
    if balance_info and balance_info.get("monthly_balances"):
        mb = balance_info["monthly_balances"]
        for label in months:
            balances.append(round(float(mb.get(label, 0)), 0) if label in mb else None)
    else:
        balances = [None] * len(months)

    # Spike detection — top-3 outflow months exceeding 1.5× median
    spike_dates: List[str] = []
    try:
        outflows = [abs(d) for d in debits if d != 0]
        if outflows:
            outflows_sorted = sorted(outflows, reverse=True)
            median = outflows_sorted[len(outflows_sorted) // 2]
            for m, d in zip(months, debits):
                if median > 0 and abs(d) >= 1.5 * median:
                    spike_dates.append(m)
            spike_dates = spike_dates[:3]
    except Exception:
        spike_dates = []

    return {
        "months": months,
        "credits": credits,
        "debits": debits,
        "balances": balances,
        "spikes": spike_dates,
        "max_credits": max_credits,
        "max_debits": max_debits,
        "median_credits": median_credits,
        "median_debits": median_debits,
    }


def _per_txn_trends(
    cust_df: Optional[pd.DataFrame],
    months: List[str],
) -> tuple:
    """For each month label (YYYY-MM), compute per-transaction max & median
    credit and debit amounts. Returns four parallel lists; uses ``None`` when
    a month has no transactions on that side (Chart.js skips ``null``).

    Edge cases handled:
      - cust_df missing/empty → all-None series.
      - Month with no credits/debits → None for that side.
      - Single transaction → median == that amount.
      - Negative or zero amounts → ignored (we use ``abs`` and drop zeros).
    """
    n = len(months)
    empty = [None] * n
    if cust_df is None or cust_df.empty:
        return empty[:], empty[:], empty[:], empty[:]
    df = cust_df.copy()
    df["tran_date"] = pd.to_datetime(df["tran_date"], errors="coerce")
    df = df.dropna(subset=["tran_date"])
    if df.empty:
        return empty[:], empty[:], empty[:], empty[:]
    df["_ym"] = df["tran_date"].dt.strftime("%Y-%m")
    df["_amt"] = df["tran_amt_in_ac"].astype(float).abs()
    df = df[df["_amt"] > 0]

    max_c, max_d, med_c, med_d = [], [], [], []
    for ym in months:
        block = df[df["_ym"] == ym]
        cr = block[block["dr_cr_indctor"] == "C"]["_amt"]
        dr = block[block["dr_cr_indctor"] == "D"]["_amt"]
        max_c.append(round(float(cr.max()), 0) if not cr.empty else None)
        max_d.append(-round(float(dr.max()), 0) if not dr.empty else None)
        med_c.append(round(float(cr.median()), 0) if not cr.empty else None)
        med_d.append(-round(float(dr.median()), 0) if not dr.empty else None)
    return max_c, max_d, med_c, med_d


def _build_heatmap(cust_df: Optional[pd.DataFrame]) -> Optional[dict]:
    """Monthly debit heatmap: rows = months (oldest→newest, up to 6), cols = day-of-month 1..31.

    Returns dict with `grid` (levels 0-4), `month_labels`, `cols` (1..31), `total_debits`,
    `window_label`, and `mode` ("monthly" or "weekly" — currently always monthly).
    """
    if cust_df is None or cust_df.empty:
        return None
    debits = cust_df[cust_df["dr_cr_indctor"] == "D"].copy()
    if debits.empty:
        return None
    debits["tran_date"] = pd.to_datetime(debits["tran_date"], errors="coerce")
    debits = debits.dropna(subset=["tran_date"])
    if debits.empty:
        return None

    debits["amt"] = debits["tran_amt_in_ac"].astype(float).abs()
    debits["ym"] = debits["tran_date"].dt.to_period("M")
    debits["dom"] = debits["tran_date"].dt.day.clip(1, 31)

    months_sorted = sorted(debits["ym"].unique())[-6:]
    if not months_sorted:
        return None
    window = debits[debits["ym"].isin(months_sorted)]

    n_rows = len(months_sorted)
    grid_amt: List[List[float]] = [[0.0] * 31 for _ in range(n_rows)]
    for _, row in window.iterrows():
        r = months_sorted.index(row["ym"])
        c = int(row["dom"]) - 1
        grid_amt[r][c] += float(row["amt"])

    flat = [v for row in grid_amt for v in row if v > 0]
    if not flat:
        return None
    flat_sorted = sorted(flat)
    n = len(flat_sorted)
    q1 = flat_sorted[n // 4] if n >= 4 else flat_sorted[0]
    q2 = flat_sorted[n // 2]
    q3 = flat_sorted[(3 * n) // 4] if n >= 4 else flat_sorted[-1]

    levels: List[List[int]] = [[0] * 31 for _ in range(n_rows)]
    for r in range(n_rows):
        for c in range(31):
            v = grid_amt[r][c]
            if v <= 0:
                lvl = 0
            elif v <= q1:
                lvl = 1
            elif v <= q2:
                lvl = 2
            elif v <= q3:
                lvl = 3
            else:
                lvl = 4
            levels[r][c] = lvl

    month_labels = [m.strftime("%b %y") for m in months_sorted]
    first = months_sorted[0].to_timestamp().strftime("%b %Y")
    last = months_sorted[-1].to_timestamp().strftime("%b %Y")
    window_label = first if first == last else f"{first} – {last}"

    return {
        "grid": levels,
        "month_labels": month_labels,
        "cols": list(range(1, 32)),
        "total_debits": int(len(window)),
        "window_label": window_label,
        "mode": "monthly",
    }


def _top_n_with_tail(items: list, n: int = 5) -> dict:
    """Top-n by amount; surface the tail as named metadata, not a chart slice."""
    items = sorted(items, key=lambda kv: float(kv[1]), reverse=True)
    top, rest = items[:n], items[n:]
    labels = [_pretty_category(k) for k, _ in top]
    amounts = [round(float(v), 0) for _, v in top]
    tail = None
    if rest:
        tail = {
            "count": len(rest),
            "total": round(sum(float(v) for _, v in rest), 0),
            "names": [_pretty_category(k) for k, _ in rest],
        }
    return {"labels": labels, "amounts": amounts, "tail": tail}


def _build_category_mix(report: CustomerReport, cust_df: Optional[pd.DataFrame] = None) -> Optional[dict]:
    co = report.category_overview
    if not co:
        return None
    l1 = _top_n_with_tail(list(co.items()), n=5)

    l2: Optional[dict] = None
    if cust_df is not None and not cust_df.empty and "category_of_txn_l2" in cust_df.columns:
        debits = cust_df[cust_df["dr_cr_indctor"] == "D"]
        if not debits.empty:
            grouped = (
                debits.groupby("category_of_txn_l2")["tran_amt_in_ac"]
                .sum()
                .to_dict()
            )
            l2 = _top_n_with_tail(list(grouped.items()), n=5)

    return {
        "labels": l1["labels"],
        "amounts": l1["amounts"],
        "l1": l1,
        "l2": l2,
    }


def _pretty_category(key: str) -> str:
    return key.replace("_", " ").strip()


# ---------------------------------------------------------------------------
# Analysis panel — multi-grain time-series for selectable variables
# ---------------------------------------------------------------------------

# Tier-1 variables shipped in v1. (id, label, group)
_ANALYSIS_VARS = [
    ("total_debit",       "Total Debit",        "Summary"),
    ("total_credit",      "Total Credit",       "Summary"),
    ("emi",               "EMI",                "Debit"),
    ("bounce_charges",    "Bounced / Charges",  "Debit"),
    ("salary",            "Salary",             "Credit"),
    ("recurring_credits", "Recurring Credits",  "Credit"),
    ("loan_credit",       "Loan Credit",        "Credit"),
    ("large_credit",      "Large Credits",      "Credit"),
]


def _build_analysis(report: CustomerReport, cust_df: Optional[pd.DataFrame]) -> Optional[dict]:
    if cust_df is None or cust_df.empty:
        return None

    df = cust_df.copy()
    df["_dt"] = pd.to_datetime(df["tran_date"], errors="coerce")
    df = df.dropna(subset=["_dt"])
    if df.empty:
        return None
    df["_amt"] = df["tran_amt_in_ac"].astype(float).abs()

    # Per-variable list of (date, amount) pairs.
    occ: Dict[str, List[tuple]] = {k: [] for k, _, _ in _ANALYSIS_VARS}

    debit_df = df[df["dr_cr_indctor"] == "D"]
    credit_df = df[df["dr_cr_indctor"] == "C"]
    occ["total_debit"]  = list(zip(debit_df["_dt"],  debit_df["_amt"]))
    occ["total_credit"] = list(zip(credit_df["_dt"], credit_df["_amt"]))

    # EMI — distribute each EMI's avg amount across its dates list.
    for emi in (report.emis or []):
        for d in (emi.dates or []):
            ts = pd.to_datetime(str(d), errors="coerce")
            if not pd.isna(ts):
                occ["emi"].append((ts, float(emi.amount or 0)))

    # Salary
    sal = report.salary
    if sal:
        for d in (sal.dates or []):
            ts = pd.to_datetime(str(d), errors="coerce")
            if not pd.isna(ts):
                occ["salary"].append((ts, float(sal.avg_amount or 0)))

    # Recurring non-salary credits
    for tm in (report.top_merchants or []):
        if tm.get("type") != "C" or tm.get("count", 0) < 2:
            continue
        if sal and tm.get("name", "").upper().find((sal.narration or "")[:8].upper()) >= 0:
            continue
        dates = _merchant_credit_dates(cust_df, tm)
        avg = float(tm.get("avg") or 0)
        for d in dates:
            ts = pd.to_datetime(str(d), errors="coerce")
            if not pd.isna(ts):
                occ["recurring_credits"].append((ts, avg))

    # Event-driven series
    for ev in (report.events or []):
        et = ev.get("type")
        ts = pd.to_datetime(str(ev.get("date")), errors="coerce")
        if pd.isna(ts):
            continue
        amt = float(ev.get("amount") or 0)
        if et in ("loan_disbursal", "loan_redistribution_suspect", "post_disbursement_usage"):
            occ["loan_credit"].append((ts, amt))
        elif et == "large_single_credit":
            occ["large_credit"].append((ts, amt))
        elif et == "ecs_bounce":
            occ["bounce_charges"].append((ts, amt))

    # Bucket helpers
    def _bucket_key(ts, grain: str) -> str:
        if grain == "monthly":
            return ts.strftime("%Y-%m")
        if grain == "weekly":
            iso = ts.isocalendar()
            return f"{iso[0]}-W{int(iso[1]):02d}"
        return ts.strftime("%Y-%m-%d")

    def _all_buckets(grain: str) -> List[str]:
        d_min, d_max = df["_dt"].min(), df["_dt"].max()
        if pd.isna(d_min) or pd.isna(d_max):
            return []
        if grain == "monthly":
            rng = pd.date_range(d_min.normalize().replace(day=1), d_max, freq="MS")
            return [d.strftime("%Y-%m") for d in rng]
        if grain == "weekly":
            start = d_min - pd.Timedelta(days=int(d_min.weekday()))
            rng = pd.date_range(start.normalize(), d_max, freq="W-MON")
            return [f"{d.isocalendar()[0]}-W{int(d.isocalendar()[1]):02d}" for d in rng]
        rng = pd.date_range(d_min.normalize(), d_max.normalize(), freq="D")
        return [d.strftime("%Y-%m-%d") for d in rng]

    grains: Dict[str, dict] = {}
    for grain in ("monthly", "weekly", "daily"):
        buckets = _all_buckets(grain)
        idx = {b: i for i, b in enumerate(buckets)}
        series: Dict[str, dict] = {}
        for vid, _, _ in _ANALYSIS_VARS:
            amt = [0.0] * len(buckets)
            cnt = [0] * len(buckets)
            for ts, a in occ[vid]:
                key = _bucket_key(ts, grain)
                i = idx.get(key)
                if i is None:
                    continue
                amt[i] += float(a)
                cnt[i] += 1
            series[vid] = {
                "amt":   [round(v, 0) for v in amt],
                "count": cnt,
            }
        grains[grain] = {"buckets": buckets, "series": series}

    return {
        "vars": [{"id": vid, "label": lbl, "group": grp} for vid, lbl, grp in _ANALYSIS_VARS],
        "groups": ["Summary", "Debit", "Credit"],
        "grains": grains,
        "defaults": ["total_debit", "total_credit"],
    }


# ---------------------------------------------------------------------------
# Recurring debits / credits
# ---------------------------------------------------------------------------

def _lookup_l2_from_sample(cust_df: Optional[pd.DataFrame], sample: Optional[dict]) -> Optional[str]:
    """Return category_of_txn_l2 for the row in cust_df matching the given sample
    transaction (by date, amount, and narration prefix). Returns None if no match
    or if cust_df has no L2 column.
    """
    if cust_df is None or cust_df.empty or not isinstance(sample, dict):
        return None
    if "category_of_txn_l2" not in cust_df.columns:
        return None
    date = sample.get("date")
    amount = sample.get("amount")
    narr = (sample.get("narration") or "")[:40]
    if not date or amount is None:
        return None
    try:
        dt = pd.to_datetime(date, errors="coerce")
        if pd.isna(dt):
            return None
        amt = float(amount)
    except Exception:
        return None
    m = (
        (pd.to_datetime(cust_df["tran_date"], errors="coerce") == dt)
        & (pd.to_numeric(cust_df["tran_amt_in_ac"], errors="coerce").round(2) == round(amt, 2))
    )
    if narr:
        m &= cust_df["tran_partclr"].astype(str).str[:40] == narr
    matched = cust_df[m]
    if matched.empty:
        return None
    val = str(matched.iloc[0]["category_of_txn_l2"] or "").strip()
    if not val or val.lower() in {"nan", "null", "none"}:
        return None
    return val


def _lookup_l2_for_merchant(cust_df: Optional[pd.DataFrame], tm: dict) -> Optional[str]:
    """Return the most common category_of_txn_l2 across cust_df rows whose
    narration contains the merchant name. Returns None if no match.
    """
    if cust_df is None or cust_df.empty or "category_of_txn_l2" not in cust_df.columns:
        return None
    name = (tm.get("name") or "").strip()
    if len(name) < 4:
        return None
    mask = cust_df["tran_partclr"].astype(str).str.contains(name[:20], case=False, na=False, regex=False)
    matched = cust_df[mask]
    if matched.empty:
        return None
    vals = matched["category_of_txn_l2"].astype(str).str.strip()
    vals = vals[~vals.str.lower().isin({"", "nan", "null", "none"})]
    if vals.empty:
        return None
    return vals.mode().iloc[0]


def _apply_l2(row: dict, cust_df: Optional[pd.DataFrame], sample: Optional[dict]) -> dict:
    l2 = _lookup_l2_from_sample(cust_df, sample)
    if l2:
        row["category"] = l2
    return row


def _build_recurring_debits(report: CustomerReport, cust_df: Optional[pd.DataFrame] = None) -> Optional[List[dict]]:
    rows: List[dict] = []
    for emi in (report.emis or []):
        rows.append(_apply_l2(_recurring_row(emi.name, "EMI", emi.frequency, emi.amount, emi.sample_transaction, emi.dates), cust_df, emi.sample_transaction))
    for bill in (report.bills or []):
        rows.append(_apply_l2(_recurring_row(bill.bill_type, "Utility", bill.frequency, bill.avg_amount, bill.sample_transaction, bill.dates), cust_df, bill.sample_transaction))
    if report.rent:
        rows.append(_apply_l2(_recurring_row("Rent Payment", "Rent", report.rent.frequency, report.rent.amount, report.rent.sample_transaction, report.rent.dates), cust_df, report.rent.sample_transaction))
    return rows or None


def _build_recurring_credits(report: CustomerReport, rg_salary_data: Optional[dict], cust_df: Optional[pd.DataFrame] = None) -> Optional[List[dict]]:
    rows: List[dict] = []
    sal = report.salary
    if sal:
        merchant = ""
        if rg_salary_data and rg_salary_data.get("rg_sal"):
            merchant = rg_salary_data["rg_sal"].get("merchant") or ""
        if not merchant and sal.narration:
            from utils.narration_utils import extract_recipient_name, clean_narration
            merchant = extract_recipient_name(sal.narration) or clean_narration(sal.narration) or ""
        win = _recurring_window(sal.dates)
        sal_l2 = _lookup_l2_from_sample(cust_df, sal.latest_transaction or sal.sample_transaction)
        rows.append({
            "name": (merchant[:40] or sal.narration[:40] or "Salary"),
            "category": sal_l2 or "Salary",
            "category_class": "salary",
            "frequency": _frequency_label_with_range(sal.frequency, win),
            "last_seen": win["last_seen"] or _fmt_full_date((sal.latest_transaction or sal.sample_transaction or {}).get("date")),
            "next_expected": win["next_window"],
            "avg_amount": round(sal.avg_amount, 0),
        })
    # Recurring non-salary credits from top_merchants (type=C with count >= 2)
    for tm in (report.top_merchants or []):
        if tm.get("type") != "C":
            continue
        if tm.get("count", 0) < 2:
            continue
        # Skip if this looks like the salary entry (avoid duplication)
        if sal and tm.get("name", "").upper().find((sal.narration or "")[:8].upper()) >= 0:
            continue
        dates = _merchant_credit_dates(cust_df, tm)
        win = _recurring_window(dates)
        tm_l2 = _lookup_l2_for_merchant(cust_df, tm)
        rows.append({
            "name": tm.get("name", "")[:40] or "Recurring credit",
            "category": tm_l2 or "Other",
            "category_class": "inv",
            "frequency": _frequency_label_with_range(tm.get("count", 0), win),
            "last_seen": win["last_seen"],
            "next_expected": win["next_window"],
            "avg_amount": round(float(tm.get("avg", 0)), 0),
        })
    return rows or None


def _merchant_credit_dates(cust_df: Optional[pd.DataFrame], tm: dict) -> List[str]:
    """Resolve occurrence dates for a credit merchant by matching the
    transaction df against the merchant's similar_narrations (or, as a
    fallback, against the representative name).
    """
    if cust_df is None or cust_df.empty:
        return []
    df = cust_df[cust_df.get("dr_cr_indctor") == "C"]
    if df.empty:
        return []
    similar = [str(s) for s in (tm.get("similar_narrations") or []) if s]
    name = str(tm.get("name") or "").strip()
    narr = df["tran_partclr"].astype(str)
    if similar:
        mask = narr.isin(similar)
    elif name:
        mask = narr.str.contains(re.escape(name), case=False, na=False)
    else:
        return []
    matched = df[mask]
    if matched.empty:
        return []
    return [d.strftime("%Y-%m-%d") for d in pd.to_datetime(matched["tran_date"], errors="coerce").dropna()]


def _recurring_row(name: str, category: str, frequency: int, amount: float, sample: dict, dates: Optional[List[str]] = None) -> dict:
    win = _recurring_window(dates or [])
    last_seen = win["last_seen"]
    if not last_seen and isinstance(sample, dict):
        last_seen = _fmt_full_date(sample.get("date"))
    cat_class = {"EMI": "emi", "Rent": "cat", "Utility": "cat"}.get(category, "cat")
    return {
        "name": (name or "").strip()[:40] or category,
        "category": category,
        "category_class": cat_class,
        "frequency": _frequency_label_with_range(frequency, win),
        "last_seen": last_seen,
        "next_expected": win["next_window"],
        "avg_amount": round(float(amount or 0), 0),
    }


# ---------------------------------------------------------------------------
# Loan cards
# ---------------------------------------------------------------------------

def _build_loan_cards(report: CustomerReport) -> Optional[List[dict]]:
    emis = report.emis or []
    if not emis:
        return None
    n_months = len(report.monthly_cashflow or []) or 6
    bounce_events = (report.events or [])
    from utils.narration_utils import _normalize_for_bucket, is_generic_merchant
    cards = []
    for emi in emis:
        emi_norm = _normalize_for_bucket(emi.name or "")
        if is_generic_merchant(emi.name or "") or len(emi_norm) < 4:
            bounces = 0
        else:
            bounces = sum(
                1 for ev in bounce_events
                if ev.get("type") == "ecs_bounce"
                and emi_norm in _normalize_for_bucket(ev.get("description", "") or "")
            )
        sample = emi.sample_transaction or {}
        emi_day = _day_of_month(sample.get("date") if isinstance(sample, dict) else None)
        cards.append({
            "name": emi.name[:50] or "EMI",
            "amount": round(float(emi.amount), 0),
            "emi_day": emi_day,
            "months_paid": min(emi.frequency, n_months),
            "months_total": n_months,
            "bounces": bounces,
            "bounces_color": "red" if bounces > 0 else "green",
        })
    return cards or None


def _build_loan_disbursals(report: CustomerReport) -> Optional[List[dict]]:
    events = report.events or []
    relevant = [
        ev for ev in events
        if ev.get("type") in ("loan_disbursal", "loan_redistribution_suspect", "post_disbursement_usage")
    ]
    if not relevant:
        return None

    # Tag each event with its extracted lender, then two-stage group:
    # exact bucket on digit-preserving normalized name, then tight fuzzy(88)
    # across bucket leaders. Avoids the over-merge produced by the legacy
    # token_set_ratio>=70 pass on short generic lender tokens.
    from utils.narration_utils import exact_then_fuzzy_group
    enriched = []
    for ev in relevant:
        enriched.append({
            "ev": ev,
            "lender": _extract_lender(ev.get("description", "")),
        })
    rep_map = exact_then_fuzzy_group([e["lender"] for e in enriched], threshold=88)

    buckets: Dict[str, dict] = {}
    for e in enriched:
        rep = rep_map.get(e["lender"], e["lender"])
        ev = e["ev"]
        amt = float(ev.get("amount") or 0)
        b = buckets.get(rep)
        if not b:
            b = {
                "lender": rep,
                "amount": 0.0,
                "count": 0,
                "first_date": "",
                "last_date": "",
                "month_labels": [],
                "descriptions": [],
            }
            buckets[rep] = b
        b["amount"] += amt
        b["count"] += 1
        d = ev.get("date") or ""
        if d and (not b["first_date"] or d < b["first_date"]):
            b["first_date"] = d
        if d and d > b["last_date"]:
            b["last_date"] = d
        if d:
            ts = pd.to_datetime(str(d), errors="coerce")
            if not pd.isna(ts):
                ml = ts.strftime("%b'%y")
                if ml not in b["month_labels"]:
                    b["month_labels"].append(ml)
        desc = (ev.get("description") or "").strip()
        if desc:
            b["descriptions"].append(desc)

    cards: List[dict] = []
    for b in buckets.values():
        sorted_months = sorted(
            b["month_labels"],
            key=lambda m: pd.to_datetime("01-" + m, format="%d-%b'%y", errors="coerce"),
        )
        month_str = " · ".join(sorted_months[:4]) + (" …" if len(sorted_months) > 4 else "")
        bullets = [d[:160] for d in b["descriptions"][:5]]
        cards.append({
            "lender": b["lender"],
            "amount": round(b["amount"], 0),
            "count": b["count"],
            "month_label": month_str,
            "details": bullets,
        })
    cards.sort(key=lambda c: c["amount"], reverse=True)
    return cards or None


# ---------------------------------------------------------------------------
# Top remitters
# ---------------------------------------------------------------------------

def _build_credit_remitters(report: CustomerReport) -> Optional[dict]:
    return _build_remitters(report, direction="C")


def _build_debit_beneficiaries(report: CustomerReport) -> Optional[dict]:
    return _build_remitters(report, direction="D")


def _build_remitters(report: CustomerReport, direction: str) -> Optional[dict]:
    tms = [t for t in (report.top_merchants or []) if t.get("type") == direction]
    if not tms:
        return None
    tms_sorted = sorted(tms, key=lambda t: float(t.get("total", 0)), reverse=True)[:5]
    items = []
    for i, tm in enumerate(tms_sorted, 1):
        items.append({
            "rank": i,
            "name": (tm.get("name") or "")[:40] or "—",
            "meta": f"{tm.get('count', 0)} txn",
            "amount": round(float(tm.get("total", 0)), 0),
        })
    return {
        "labels": [it["name"] for it in items],
        "amounts": [it["amount"] for it in items],
        "rows": items,
    }


# ---------------------------------------------------------------------------
# Transactions table
# ---------------------------------------------------------------------------

def _build_transactions(report: CustomerReport, cust_df: Optional[pd.DataFrame]) -> Optional[dict]:
    if cust_df is None or cust_df.empty:
        return None
    # Prefer source eod_balance column; fall back to cumsum-from-zero.
    df_asc = cust_df.sort_values("tran_date", ascending=True).copy()
    if "eod_balance" in df_asc.columns and df_asc["eod_balance"].notna().any():
        df_asc["_running"] = pd.to_numeric(df_asc["eod_balance"], errors="coerce").ffill().fillna(0)
    else:
        df_asc["_signed"] = df_asc.apply(
            lambda r: float(r.get("tran_amt_in_ac", 0) or 0)
            * (1 if r.get("dr_cr_indctor") == "C" else -1),
            axis=1,
        )
        df_asc["_running"] = df_asc["_signed"].cumsum()
    df = df_asc.sort_values("tran_date", ascending=False).head(500)
    rows = []
    bounce_re = re.compile(r"BOUNCE|RTN|RETURN", re.IGNORECASE)
    for _, r in df.iterrows():
        narr = str(r.get("tran_partclr", "") or "")
        cls = _classify_txn(narr, str(r.get("category_of_txn", "") or ""))
        is_dr = r.get("dr_cr_indctor") == "D"
        amt = float(r.get("tran_amt_in_ac", 0) or 0)
        cat_l1 = (str(r.get("category_of_txn", "") or "").strip() or "—")[:24]
        l2_raw = str(r.get("category_of_txn_l2", "") or "").strip()
        if not l2_raw or l2_raw.lower() in {"nan", "null", "none"}:
            cat_l2 = cat_l1
        else:
            cat_l2 = l2_raw[:24]
        rows.append({
            "date": r["tran_date"].strftime("%d-%b"),
            "date_raw": r["tran_date"].strftime("%Y-%m-%d"),
            "narration": narr[:80],
            "narration_full": narr,
            "cls": cls,
            "category": cat_l1,
            "category_l2": cat_l2,
            "debit": round(amt, 0) if is_dr else None,
            "credit": round(amt, 0) if not is_dr else None,
            "balance": round(float(r.get("_running", 0) or 0), 0),
            "flag": "bounce" if bounce_re.search(narr) else "",
        })
    from tools.category.registry import L2_TO_L1
    return {
        "rows": rows,
        "total": int(report.meta.transaction_count or len(rows)),
        "showing": len(rows),
        "l2_options": sorted(L2_TO_L1.keys()),
        "l2_to_l1": dict(L2_TO_L1),
    }


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _months_from_cashflow(report: CustomerReport) -> List[str]:
    cf = report.monthly_cashflow or []
    return [m.get("month", "") for m in cf]


def _fallback_months(n: int) -> List[str]:
    return [f"M{i+1}" for i in range(n)]


def _frequency_label(count: int) -> str:
    if count >= 5:
        return "Monthly"
    if count >= 2:
        return "Recurring"
    return "Ad-hoc"


def _frequency_label_with_range(count: int, win: dict) -> str:
    base = _frequency_label(count)
    rng = win.get("day_range") if win else None
    if rng:
        return f"{base} · day {rng[0]}-{rng[1]}"
    return base


def _fmt_full_date(value: Any) -> str:
    if not value:
        return ""
    try:
        dt = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(dt):
            return ""
        return dt.strftime("%d %b '%y")
    except Exception:
        return ""


def _recurring_window(dates: List[str]) -> dict:
    """Compute mode-day-of-month window, latest date, and next-expected window
    from a list of occurrence date strings.

    Returns a dict with keys: day_range (tuple|None), last_seen (str),
    next_window (str), median_gap_days (int|None).
    """
    out = {"day_range": None, "last_seen": "", "next_window": "", "median_gap_days": None}
    if not dates:
        return out
    parsed: List[Any] = []
    for d in dates:
        try:
            ts = pd.to_datetime(str(d), errors="coerce")
            if not pd.isna(ts):
                parsed.append(ts)
        except Exception:
            continue
    if not parsed:
        return out
    parsed.sort()
    days = [int(ts.day) for ts in parsed]
    # Mode day-of-month
    from collections import Counter
    mode_day = Counter(days).most_common(1)[0][0]
    lo = max(1, mode_day - 5)
    hi = min(31, mode_day + 5)
    out["day_range"] = (lo, hi)
    # Latest
    latest = parsed[-1]
    out["last_seen"] = latest.strftime("%d %b '%y")
    # Median gap (days) between consecutive occurrences; default to 30 if <2 points.
    if len(parsed) >= 2:
        gaps = [(parsed[i] - parsed[i - 1]).days for i in range(1, len(parsed))]
        gaps_sorted = sorted(g for g in gaps if g > 0)
        if gaps_sorted:
            mid = len(gaps_sorted) // 2
            median_gap = gaps_sorted[mid] if len(gaps_sorted) % 2 else (gaps_sorted[mid - 1] + gaps_sorted[mid]) // 2
        else:
            median_gap = 30
    else:
        median_gap = 30
    out["median_gap_days"] = int(median_gap)
    nxt_lo = latest + timedelta(days=int(median_gap) - 5)
    nxt_hi = latest + timedelta(days=int(median_gap) + 5)
    yr = nxt_hi.strftime("'%y")
    if nxt_lo.month == nxt_hi.month and nxt_lo.year == nxt_hi.year:
        out["next_window"] = f"{nxt_lo.day}–{nxt_hi.day} {nxt_hi.strftime('%b')} {yr}"
    else:
        out["next_window"] = f"{nxt_lo.strftime('%d %b')} – {nxt_hi.strftime('%d %b')} {yr}"
    return out


def _day_of_month(value: Any) -> str:
    if not value:
        return "—"
    try:
        dt = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(dt):
            return "—"
        d = dt.day
        suffix = "th" if 10 <= d <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return f"{d}{suffix}"
    except Exception:
        return "—"


def _extract_lender(description: str) -> str:
    """Pull lender name from a loan-disbursal event description.

    Event descriptions are formatted as ``"... (<raw_narration>)"`` so we first
    feed the narration in parens through the shared ``extract_recipient_name``
    so output stays consistent with EMI / recurring rows. Falls back to the
    legacy "from <Name>" regex, then a generic "Lender" sentinel.
    """
    if not description:
        return "Lender"
    from utils.narration_utils import extract_recipient_name, clean_narration
    paren = re.search(r"\(([^()]+)\)\s*$", description)
    if paren:
        narr = paren.group(1).strip()
        name = extract_recipient_name(narr) or clean_narration(narr)
        if name and len(name) > 3:
            return name
    m = re.search(r"from ([A-Z][A-Za-z/&\- ]{2,40})", description)
    if m:
        return m.group(1).strip()
    return "Lender"


def _classify_txn(narration: str, category: str) -> str:
    from tools.rules import is_salary_credit, is_emi_debit
    upper = (narration or "").upper()
    if is_salary_credit(category, narration):
        return "salary"
    if is_emi_debit(category, narration):
        return "emi"
    if "DREAM11" in upper or "MPL" in upper or "BETTING" in upper or "GAMING" in upper:
        return "fraud"
    if "MF" in upper or "SIP" in upper or "DIVIDEND" in upper or "ZERODHA" in upper:
        return "inv"
    if "UPI" in upper or "IMPS" in upper or "NEFT" in upper:
        return "upi"
    return "other"
