"""Pure Python analytics functions returning structured dicts.

Only the functions consumed by the report pipeline live here:
  - get_spending_by_category  (customer_report_builder)
  - get_cash_flow             (customer_report_builder)
  - get_balance_trend         (bank_v2_view_model)
"""

from typing import Dict, Any
from data.loader import get_transactions_df
import pandas as pd


def get_spending_by_category(customer_id: int, category: str = None) -> Dict[str, Any]:
    df = get_transactions_df()
    if category:
        filtered = df[
            (df['cust_id'] == customer_id) &
            (df['dr_cr_indctor'] == 'D') &
            (df['category_of_txn'] == category)
        ]
        return {
            "customer_id": customer_id,
            "category": category,
            "category_spending": float(filtered['tran_amt_in_ac'].sum()),
            "transaction_count": len(filtered),
            "currency": "INR"
        }
    else:
        filtered = df[
            (df['cust_id'] == customer_id) &
            (df['dr_cr_indctor'] == 'D')
        ]
        by_category = filtered.groupby('category_of_txn')['tran_amt_in_ac'].sum().to_dict()
        by_category = {cat: float(amount) for cat, amount in by_category.items()}
        transactions_by_category = filtered.groupby('category_of_txn').size().to_dict()
        result = {
            "customer_id": customer_id,
            "all_categories_spending": by_category,
            "transactions_by_category": transactions_by_category,
            "total_spending": float(filtered['tran_amt_in_ac'].sum()),
            "currency": "INR"
        }
        return result


def get_balance_trend(customer_id: int) -> Dict[str, Any]:
    """Calculate running balance over time for a customer."""
    df = get_transactions_df()
    cust_df = df[df['cust_id'] == customer_id].copy()

    if len(cust_df) == 0:
        return {
            "customer_id": customer_id,
            "balance_series": [],
            "monthly_balances": {},
            "min_balance": 0,
            "max_balance": 0,
            "final_balance": 0,
            "trend": "no_data",
            "currency": "INR"
        }

    cust_df = cust_df.sort_values('tran_date')
    if 'eod_balance' in cust_df.columns and cust_df['eod_balance'].notna().any():
        cust_df['running_balance'] = pd.to_numeric(cust_df['eod_balance'], errors='coerce').ffill().fillna(0)
    else:
        cust_df['signed_amount'] = cust_df.apply(
            lambda r: r['tran_amt_in_ac'] if r['dr_cr_indctor'] == 'C' else -r['tran_amt_in_ac'],
            axis=1
        )
        cust_df['running_balance'] = cust_df['signed_amount'].cumsum()

    # Monthly end balances
    cust_df['month'] = cust_df['tran_date'].str[:7]
    monthly_balances = cust_df.groupby('month')['running_balance'].last().to_dict()

    # Determine trend
    balances = list(monthly_balances.values())
    if len(balances) >= 2:
        if balances[-1] > balances[0]:
            trend = "increasing"
        elif balances[-1] < balances[0]:
            trend = "decreasing"
        else:
            trend = "stable"
    else:
        trend = "insufficient_data"

    return {
        "customer_id": customer_id,
        "balance_series": cust_df[['tran_date', 'running_balance']].tail(50).to_dict('records'),
        "monthly_balances": {k: float(v) for k, v in monthly_balances.items()},
        "min_balance": float(cust_df['running_balance'].min()),
        "max_balance": float(cust_df['running_balance'].max()),
        "final_balance": float(cust_df['running_balance'].iloc[-1]),
        "trend": trend,
        "currency": "INR"
    }


def get_cash_flow(customer_id: int) -> Dict[str, Any]:
    """Get monthly cash flow summary (inflows vs outflows)."""
    df = get_transactions_df()
    cust_df = df[df['cust_id'] == customer_id].copy()

    if len(cust_df) == 0:
        return {
            "customer_id": customer_id,
            "monthly_cash_flow": {},
            "avg_monthly_inflow": 0,
            "avg_monthly_outflow": 0,
            "avg_net_cash_flow": 0,
            "currency": "INR"
        }

    cust_df['tran_date'] = pd.to_datetime(cust_df['tran_date'])
    cust_df['month'] = cust_df['tran_date'].dt.to_period('M').dt.to_timestamp()

    credits = cust_df[cust_df['dr_cr_indctor'] == 'C']
    debits = cust_df[cust_df['dr_cr_indctor'] == 'D']

    monthly_inflows = credits.groupby('month')['tran_amt_in_ac'].sum()
    monthly_outflows = debits.groupby('month')['tran_amt_in_ac'].sum()

    all_months = sorted(set(monthly_inflows.index) | set(monthly_outflows.index))

    monthly_cash_flow = {}
    for month in all_months:
        inflow = monthly_inflows.get(month, 0)
        outflow = monthly_outflows.get(month, 0)
        month_key = month.strftime('%Y-%m')  # Convert to "2025-07"
        monthly_cash_flow[month_key] = {
            "inflow": float(inflow),
            "outflow": float(outflow),
            "net": float(inflow - outflow)
        }

    return {
        "customer_id": customer_id,
        "monthly_cash_flow": monthly_cash_flow,
        "avg_monthly_inflow": float(monthly_inflows.mean()) if len(monthly_inflows) > 0 else 0,
        "avg_monthly_outflow": float(monthly_outflows.mean()) if len(monthly_outflows) > 0 else 0,
        "avg_net_cash_flow": float((monthly_inflows.mean() if len(monthly_inflows) > 0 else 0) - (monthly_outflows.mean() if len(monthly_outflows) > 0 else 0)),
        "currency": "INR"
    }
