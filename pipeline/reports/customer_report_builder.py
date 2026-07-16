"""Customer report builder - data collection without LLM.

This module collects factual data from existing tools and populates
the CustomerReport schema. NO LLM calls are made here - this is
purely deterministic data aggregation.
"""

from datetime import datetime
from typing import Optional, Tuple

from data.loader import get_transactions_df
from tools.analytics import (
    get_spending_by_category,
    get_cash_flow,
)
from tools.transaction_fetcher import fetch_transaction_summary
from tools.category.resolver import resolve_category_presence
from schemas.customer_report import (
    CustomerReport,
    ReportMeta,
    SalaryBlock,
    EMIBlock,
    BillBlock,
    RentBlock,
)
from tools.account_quality import compute_account_quality
from tools.event_detector import detect_events
from tools.merchant_features import compute_all_merchant_features
from utils.helpers import safe_call


def build_customer_report(customer_id: int, months: int = 6) -> CustomerReport:
    """
    Build a customer report by collecting data from existing tools.

    This function orchestrates calls to existing analytics and detection
    tools, adapting their outputs to the CustomerReport schema.

    Args:
        customer_id: Customer identifier
        months: Analysis period in months (default 6)

    Returns:
        CustomerReport with all available sections populated
    """
    # 1. Get transaction count for meta
    df = get_transactions_df()
    cust_df = df[df['cust_id'] == customer_id]
    transaction_count = len(cust_df)

    # 2. Get party name if available
    prty_name = None
    if 'prty_name' in cust_df.columns and len(cust_df) > 0:
        prty_name = cust_df['prty_name'].iloc[0]
        if prty_name and str(prty_name).lower() not in ['nan', 'none', '']:
            prty_name = str(prty_name)
        else:
            prty_name = None

    # 3. Build report meta
    meta = ReportMeta(
        customer_id=customer_id,
        prty_name=prty_name,
        generated_at=datetime.now().isoformat(),
        analysis_period=f"Last {months} months",
        currency="INR",
        transaction_count=transaction_count
    )

    # 3. Get category overview (reuse get_spending_by_category)
    category_overview = safe_call(_get_category_overview, customer_id)

    # 4. Get monthly cashflow (reuse get_cash_flow)
    monthly_cashflow = safe_call(_get_monthly_cashflow, customer_id)

    # Fetch the transaction summary once and share it across the sections below
    # (top merchants + salary) instead of computing the fuzzy grouping twice.
    summary = safe_call(fetch_transaction_summary, customer_id)

    # 5. Get top merchants (from transaction summary high-freq groups)
    top_merchants = safe_call(_get_top_merchants, summary)

    # 6. Get salary block (from transaction summary)
    salary_block = safe_call(_get_salary_block, summary, cust_df)

    # 7. Get EMI block (via category presence)
    emis = safe_call(_get_emi_block, cust_df)

    # 8. Get rent block (via category presence)
    rent_block = safe_call(_get_rent_block, customer_id)

    # 9. Get bills block (via category presence for utilities)
    bills = safe_call(_get_bills_block, customer_id)

    # 10. Get merchant-level behavioral features
    merchant_features = safe_call(compute_all_merchant_features, customer_id) or None

    base_report = CustomerReport(
        meta=meta,
        category_overview=category_overview,
        monthly_cashflow=monthly_cashflow,
        top_merchants=top_merchants,
        salary=salary_block,
        emis=emis,
        rent=rent_block,
        bills=bills,
        merchant_features=merchant_features,
    )

    # Compute account quality after base report is built (needs emis/bills/rent)
    account_quality = safe_call(compute_account_quality, customer_id, customer_report=base_report)

    # Detect semantic events from raw narrations (PF withdrawal, post-salary routing, etc.)
    events = safe_call(detect_events, customer_id) or None

    # Augment with L2-tagged loan disbursals that the keyword rules missed.
    # detect_events already produces loan_disbursal events from
    # LOAN_DISBURSEMENT_KEYWORDS; this catches credit rows where L2
    # (hybrid regex+DL tagger or admin edit) marks Loan_Disburse but the
    # narration lacks a disbursal keyword. Dedup against existing events
    # by (date, amount, narration). On failure keep the pre-augmentation events.
    events = safe_call(_augment_disbursal_events_from_l2, cust_df, events or [], default=events)

    updates = {}
    if account_quality:
        updates["account_quality"] = account_quality
    if events:
        updates["events"] = events

    final_report = base_report.model_copy(update=updates) if updates else base_report

    # Deterministic review checklist — computed here so renderers stay pure.
    # Thread the already-filtered cust_df so the checklist reuses it instead of
    # re-loading + re-filtering the frame for its transaction-level checks.
    from pipeline.reports.checklist_builder import compute_checklist
    checklist = safe_call(compute_checklist, final_report, cust_df)
    if checklist:
        final_report = final_report.model_copy(update={"checklist": checklist})

    return final_report


def _get_category_overview(customer_id: int) -> Optional[dict]:
    """Get category spending breakdown."""
    category_data = get_spending_by_category(customer_id)
    overview = category_data.get('all_categories_spending')
    return overview if overview else None


def _get_monthly_cashflow(customer_id: int) -> Optional[list]:
    """Get monthly cashflow data."""
    cashflow_data = get_cash_flow(customer_id)
    monthly_data = cashflow_data.get('monthly_cash_flow', {})

    if not monthly_data:
        return None

    # Convert to list format for template
    cashflow_list = [
        {"month": k, "inflow": v.get('inflow', 0), "outflow": v.get('outflow', 0), "net": v.get('net', 0)}
        for k, v in sorted(monthly_data.items())
    ]
    return cashflow_list if cashflow_list else None


def _get_top_merchants(summary) -> Optional[list]:
    """Get top merchants from high-frequency transaction groups.

    Returns a flat list with a 'type' field ('D'/'C') so templates can
    filter into separate debit-merchant and credit-source tables.
    Groups are ranked by hybrid score (frequency x amount).
    Takes top 5 debits and top 5 credits.
    """
    if not summary or not summary.high_frequency_transactions:
        return None

    debits = [t for t in summary.high_frequency_transactions if t.transaction_type == 'D']
    credits = [t for t in summary.high_frequency_transactions if t.transaction_type == 'C']

    debits.sort(key=lambda t: t.score, reverse=True)
    credits.sort(key=lambda t: t.score, reverse=True)

    top = debits[:5] + credits[:5]

    top_merchants = [
        {
            "name": t.representative_narration,
            "count": t.count,
            "total": t.total_amount,
            "avg": t.average_amount,
            "type": t.transaction_type,
            "score": t.score,
            "similar_narrations": list(t.similar_narrations or []),
        }
        for t in top
    ]
    return top_merchants if top_merchants else None


def _get_salary_block(summary, cust_df) -> Optional[SalaryBlock]:
    """Get salary information from transaction summary."""
    if not summary or not summary.salary_summary:
        return None

    salary = summary.salary_summary

    # Latest salary transaction + all occurrence dates in a single scan.
    latest_transaction, all_salary_dates = _salary_scan(cust_df)
    return SalaryBlock(
        avg_amount=salary.average_amount,
        frequency=salary.transaction_count,
        narration=salary.narrations[0] if salary.narrations else "",
        sample_transaction={
            "amount": salary.average_amount,
            "total": salary.total_amount
        },
        latest_transaction=latest_transaction,
        dates=all_salary_dates,
    )


def _salary_scan(cust_df) -> Tuple[Optional[dict], list]:
    """Single pass over the customer's credits, returning
    ``(latest_salary_transaction, all_salary_dates)``.

    Merges the former ``_get_latest_salary_transaction`` /
    ``_get_all_salary_dates`` helpers (which each scanned the frame with the
    same salary predicate). Behaviour is preserved exactly: the latest
    transaction is the highest-date salary credit (date / amount /
    narration[:80]); ``dates`` are the stripped, non-empty dates in row order.
    """
    try:
        from utils.narration_utils import is_salary_narration
        from tools.category.registry import has_role

        salary_txns = []
        dates = []
        for _, row in cust_df.iterrows():
            if row.get('dr_cr_indctor') != 'C':
                continue
            l2 = row.get('category_of_txn_l2', '')
            narration = str(row.get('tran_partclr', ''))
            if has_role(l2, 'salary') or is_salary_narration(narration):
                salary_txns.append({
                    'date': str(row.get('tran_date', '')),
                    'amount': float(row.get('tran_amt_in_ac', 0)),
                    'narration': narration[:80] if narration else ''
                })
                d = str(row.get('tran_date', '')).strip()
                if d:
                    dates.append(d)

        latest = None
        if salary_txns:
            salary_txns.sort(key=lambda x: x['date'], reverse=True)
            latest = salary_txns[0]
        return latest, dates
    except Exception:
        return None, []


def _augment_disbursal_events_from_l2(cust_df, events: list) -> list:
    """Append synthetic loan_disbursal events for credit rows tagged
    ``category_of_txn_l2 == 'Loan_Disburse'`` (alias-aware) that the
    keyword-rule pass did not already emit. Dedup key: (date, round(amount), narration).
    """
    from tools.category.registry import l2_canonical
    import pandas as pd

    cust_df = cust_df[cust_df['dr_cr_indctor'] == 'C'].copy()
    if cust_df.empty or 'category_of_txn_l2' not in cust_df.columns:
        return events

    l2_mask = cust_df['category_of_txn_l2'].apply(
        lambda v: l2_canonical(v) == 'Loan_Disburse'
    )
    candidates = cust_df[l2_mask]
    if candidates.empty:
        return events

    existing_keys = set()
    for ev in events:
        if ev.get('type') == 'loan_disbursal':
            existing_keys.add((
                str(ev.get('date') or ''),
                round(float(ev.get('amount') or 0), 0),
            ))

    new_events = list(events)
    for _, row in candidates.iterrows():
        ts = pd.to_datetime(row.get('tran_date'), errors='coerce')
        if pd.isna(ts):
            continue
        amt = float(row.get('tran_amt_in_ac', 0) or 0)
        if amt <= 0:
            continue
        key = (str(ts.date()), round(amt, 0))
        if key in existing_keys:
            continue
        narr = str(row.get('tran_partclr', '') or '')
        month_label = ts.strftime('%b %Y')
        new_events.append({
            'type': 'loan_disbursal',
            'date': str(ts.date()),
            'month_label': month_label,
            'amount': round(amt, 2),
            'significance': 'high',
            'description': f"{month_label}: Loan disbursal credit received ({narr[:80]})",
        })
        existing_keys.add(key)

    return new_events


def _get_emi_block(cust_df) -> Optional[list]:
    """Detect EMI payments using the merged EMI keyword set in
    ``config.keywords.EMI_ALL_KEYWORDS`` (NACH/SPLN mandates + core-banking
    EMI narrations + home-loan EMIs). One EMIBlock per recipient group.
    """
    from config.keywords import EMI_ALL_KEYWORDS
    from tools.event_detector import _kw_to_regex
    from tools.category.registry import has_role
    from utils.narration_utils import (
        extract_recipient_name,
        clean_narration,
        exact_then_fuzzy_group,
    )

    cust_df = cust_df[cust_df['dr_cr_indctor'] == 'D'].copy()
    if cust_df.empty:
        return None

    # Signal 1: narration regex (existing keyword set).
    pattern = "|".join(_kw_to_regex(kw) for kw in EMI_ALL_KEYWORDS)
    narrations = cust_df['tran_partclr'].fillna('').astype(str).str.upper()
    narr_mask = narrations.str.contains(pattern, na=False, regex=True)

    # Signal 2: L2 category == EMI (alias-aware via has_role).
    if 'category_of_txn_l2' in cust_df.columns:
        l2_mask = cust_df['category_of_txn_l2'].apply(lambda v: has_role(v, 'emi'))
    else:
        l2_mask = narr_mask & False

    matched = cust_df[narr_mask | l2_mask].sort_values('tran_date')
    if matched.empty:
        return None

    # First pass — extract a name per matched txn
    rows: list = []
    for _, row in matched.iterrows():
        narration = str(row.get('tran_partclr', '') or '')
        name = extract_recipient_name(narration)
        if not name:
            name = clean_narration(narration) or 'EMI Payment'
        rows.append({
            'name': name,
            'narration': narration,
            'date': str(row.get('tran_date', '')),
            'amount': float(row.get('tran_amt_in_ac', 0) or 0),
            'direction': 'D',
        })

    # Two-stage grouping: exact digit-preserving bucket, then tight
    # fuzzy(88) across bucket leaders. Avoids the over-merge that
    # token_set_ratio>=70 produced on short generic lender tokens.
    rep_map = exact_then_fuzzy_group([r['name'] for r in rows], threshold=88)

    groups: dict = {}        # rep -> list of amounts
    samples: dict = {}       # rep -> first matching txn
    dates_by_name: dict = {} # rep -> list of dates
    for r in rows:
        name = rep_map.get(r['name'], r['name'])
        if name not in groups:
            groups[name] = []
            samples[name] = r
            dates_by_name[name] = []
        groups[name].append(r['amount'])
        d = r['date'].strip()
        if d:
            dates_by_name[name].append(d)

    blocks = []
    for name, amounts in groups.items():
        blocks.append(EMIBlock(
            name=name,
            amount=round(sum(amounts) / len(amounts), 2),
            frequency=len(amounts),
            sample_transaction=samples[name],
            dates=dates_by_name.get(name, []),
        ))

    return blocks if blocks else None


def _presence_stats(customer_id: int, category: str):
    """Resolve category presence and return ``(frequency, avg_amount, sample,
    dates)``, or ``None`` when the category is absent. Shared by the rent and
    utility-bill blocks (identical derivation, different schema wrapper).
    """
    result = resolve_category_presence(customer_id, category)
    if not result.get('present'):
        return None
    txn_count = result.get('transaction_count', 1)
    avg_amount = result.get('total_amount', 0) / max(1, txn_count)
    supporting = result.get('supporting_transactions', [])
    sample = supporting[0] if supporting else {}
    dates = [str(t.get('date', '')).strip() for t in supporting if t.get('date')]
    return txn_count, avg_amount, sample, dates


def _get_rent_block(customer_id: int) -> Optional[RentBlock]:
    """Detect rent payments using category presence lookup."""
    stats = _presence_stats(customer_id, "rent")
    if not stats:
        return None
    txn_count, avg_amount, sample, dates = stats
    return RentBlock(
        direction="paid",
        frequency=txn_count,
        amount=avg_amount,
        sample_transaction=sample,
        dates=dates,
    )


def _get_bills_block(customer_id: int) -> Optional[list]:
    """Detect utility bill payments using category presence lookup."""
    stats = _presence_stats(customer_id, "utilities")
    if not stats:
        return None
    txn_count, avg_amount, sample, dates = stats
    return [BillBlock(
        bill_type="Utilities",
        frequency=txn_count,
        avg_amount=avg_amount,
        sample_transaction=sample,
        dates=dates,
    )]
