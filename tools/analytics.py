"""Pure Python analytics functions returning structured dicts."""

from typing import Dict, Any, List
from data.loader import get_transactions_df
from datetime import datetime, timedelta
import pandas as pd

def debit_total(customer_id: int, months: int = 6) -> Dict[str, Any]:
    df = get_transactions_df().copy()
    df['tran_date'] = pd.to_datetime(df['tran_date'])
    filtered = df[(df['cust_id'] == customer_id) & (df['dr_cr_indctor'] == 'D')]

    # If months is not None or > 0, restrict to the last 'months' months
    if months is not None and months > 0:
        max_date = filtered['tran_date'].max()
        if pd.isnull(max_date): max_date = datetime.today()
        start_period = max_date - pd.DateOffset(months=months)
        filtered = filtered[filtered['tran_date'] >= start_period]

    # Calculate overall stats
    result = {
        "customer_id": customer_id,
        "currency": "INR"
    }

    if months is not None and months > 0:
        filtered['month'] = filtered['tran_date'].dt.to_period('M').dt.to_timestamp()
        month_group = filtered.groupby('month')['tran_amt_in_ac'].sum().sort_index(ascending=False)
        months_list = [(month_group.index.max() - pd.DateOffset(months=i)).replace(day=1)for i in range(months)]
        month_wise_spending = {
            pd.Timestamp(month).strftime("%Y-%m"): float(month_group.get(month, 0.0))
            for month in months_list
        }
        result["month_wise_spending"] = month_wise_spending
        result["total_spending"] = float(filtered['tran_amt_in_ac'].sum())
        result["transaction_count"] = int(len(filtered))
    else:
        result["total_spending"] = float(filtered['tran_amt_in_ac'].sum())
        result["transaction_count"] = int(len(filtered))

    return result


def get_total_income(customer_id: int) -> Dict[str, Any]:
    df = get_transactions_df()
    filtered = df[(df['cust_id'] == customer_id) & (df['dr_cr_indctor'] == 'C')]

    return {
        "customer_id": customer_id,
        "total_income": float(filtered['tran_amt_in_ac'].sum()),
        "transaction_count": len(filtered),
        "currency": "INR"
    }


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


def top_spending_categories(customer_id: int, top_n: int = 5) -> Dict[str, Any]:
    df = get_transactions_df()
    filtered = df[(df['cust_id'] == customer_id) & (df['dr_cr_indctor'] == 'D')]

    category_totals = filtered.groupby('category_of_txn')['tran_amt_in_ac'].sum()
    top_cats = category_totals.sort_values(ascending=False).head(top_n)

    return {
        "customer_id": customer_id,
        "top_n": top_n,
        "top_categories": {cat: float(amt) for cat, amt in top_cats.items()},
        "currency": "INR"
    }


def spending_in_date_range(
    customer_id: int,
    start_date: str,
    end_date: str
) -> Dict[str, Any]:
    df = get_transactions_df()
    filtered = df[
        (df['cust_id'] == customer_id) &
        (df['dr_cr_indctor'] == 'D') &
        (df['tran_date'] >= start_date) &
        (df['tran_date'] <= end_date)
    ]

    return {
        "customer_id": customer_id,
        "start_date": start_date,
        "end_date": end_date,
        "total_spending": float(filtered['tran_amt_in_ac'].sum()),
        "transaction_count": len(filtered),
        "currency": "INR"
    }


def list_customers() -> Dict[str, Any]:
    df = get_transactions_df()
    customers = sorted(df['cust_id'].unique().tolist())

    return {
        "customers": customers,
        "total_count": len(customers)
    }


def list_categories() -> Dict[str, Any]:
    df = get_transactions_df()
    categories = sorted(df['category_of_txn'].unique().tolist())

    return {
        "categories": categories,
        "total_count": len(categories)
    }


def get_credit_statistics(customer_id: int) -> Dict[str, Any]:
    """Get comprehensive credit/income statistics for a customer."""
    df = get_transactions_df()
    credits = df[(df['cust_id'] == customer_id) & (df['dr_cr_indctor'] == 'C')]

    if len(credits) == 0:
        return {
            "customer_id": customer_id,
            "max_credit": {"amount": 0, "source": None, "date": None},
            "second_max_credit": {"amount": 0, "source": None, "date": None},
            "avg_credit": 0,
            "median_credit": 0,
            "total_credit_count": 0,
            "monthly_avg_amount": 0,
            "monthly_avg_count": 0,
            "quarterly_avg_amount": 0,
            "currency": "INR"
        }

    # Sort by amount for top transactions
    sorted_credits = credits.sort_values('tran_amt_in_ac', ascending=False)

    # Max credit
    max_row = sorted_credits.iloc[0]
    max_credit = {
        "amount": float(max_row['tran_amt_in_ac']),
        "source": max_row['category_of_txn'],
        "date": max_row['tran_date'],
        "tran_type": max_row['tran_type']
    }

    # Second max credit
    second_max_credit = {"amount": 0, "source": None, "date": None}
    if len(sorted_credits) >= 2:
        second_row = sorted_credits.iloc[1]
        second_max_credit = {
            "amount": float(second_row['tran_amt_in_ac']),
            "source": second_row['category_of_txn'],
            "date": second_row['tran_date']
        }

    # Monthly statistics
    credits_copy = credits.copy()
    credits_copy['month'] = credits_copy['tran_date'].str[:7]
    monthly_amounts = credits_copy.groupby('month')['tran_amt_in_ac'].sum()
    monthly_counts = credits_copy.groupby('month').size()

    # Quarterly statistics
    credits_copy['quarter'] = credits_copy['tran_date'].str[:4] + '-Q' + ((credits_copy['tran_date'].str[5:7].astype(int) - 1) // 3 + 1).astype(str)
    quarterly_amounts = credits_copy.groupby('quarter')['tran_amt_in_ac'].sum()

    return {
        "customer_id": customer_id,
        "max_credit": max_credit,
        "second_max_credit": second_max_credit,
        "avg_credit": float(credits['tran_amt_in_ac'].mean()),
        "median_credit": float(credits['tran_amt_in_ac'].median()),
        "total_credit_count": len(credits),
        "monthly_avg_amount": float(monthly_amounts.mean()) if len(monthly_amounts) > 0 else 0,
        "monthly_median_amount": float(monthly_amounts.median()) if len(monthly_amounts) > 0 else 0,
        "monthly_avg_count": float(monthly_counts.mean()) if len(monthly_counts) > 0 else 0,
        "monthly_median_count": float(monthly_counts.median()) if len(monthly_counts) > 0 else 0,
        "quarterly_avg_amount": float(quarterly_amounts.mean()) if len(quarterly_amounts) > 0 else 0,
        "currency": "INR"
    }


def get_debit_statistics(customer_id: int) -> Dict[str, Any]:
    """Get comprehensive debit/spending statistics for a customer."""
    df = get_transactions_df()
    debits = df[(df['cust_id'] == customer_id) & (df['dr_cr_indctor'] == 'D')]

    if len(debits) == 0:
        return {
            "customer_id": customer_id,
            "max_debit": {"amount": 0, "category": None, "date": None},
            "avg_debit": 0,
            "median_debit": 0,
            "total_debit_count": 0,
            "monthly_avg_amount": 0,
            "monthly_avg_count": 0,
            "currency": "INR"
        }

    sorted_debits = debits.sort_values('tran_amt_in_ac', ascending=False)
    max_row = sorted_debits.iloc[0]

    debits_copy = debits.copy()
    debits_copy['month'] = debits_copy['tran_date'].str[:7]
    monthly_amounts = debits_copy.groupby('month')['tran_amt_in_ac'].sum()
    monthly_counts = debits_copy.groupby('month').size()

    return {
        "customer_id": customer_id,
        "max_debit": {
            "amount": float(max_row['tran_amt_in_ac']),
            "category": max_row['category_of_txn'],
            "date": max_row['tran_date']
        },
        "avg_debit": float(debits['tran_amt_in_ac'].mean()),
        "median_debit": float(debits['tran_amt_in_ac'].median()),
        "total_debit_count": len(debits),
        "monthly_avg_amount": float(monthly_amounts.mean()) if len(monthly_amounts) > 0 else 0,
        "monthly_median_amount": float(monthly_amounts.median()) if len(monthly_amounts) > 0 else 0,
        "monthly_avg_count": float(monthly_counts.mean()) if len(monthly_counts) > 0 else 0,
        "monthly_median_count": float(monthly_counts.median()) if len(monthly_counts) > 0 else 0,
        "currency": "INR"
    }


def get_transaction_counts(customer_id: int) -> Dict[str, Any]:
    """Get credit/debit transaction counts with monthly breakdown."""
    df = get_transactions_df()
    cust_df = df[df['cust_id'] == customer_id]

    credits = cust_df[cust_df['dr_cr_indctor'] == 'C']
    debits = cust_df[cust_df['dr_cr_indctor'] == 'D']

    # Monthly breakdown
    cust_df_copy = cust_df.copy()
    cust_df_copy['month'] = cust_df_copy['tran_date'].str[:7]

    credit_monthly = credits.copy()
    credit_monthly['month'] = credit_monthly['tran_date'].str[:7]
    debit_monthly = debits.copy()
    debit_monthly['month'] = debit_monthly['tran_date'].str[:7]

    return {
        "customer_id": customer_id,
        "total_credits": len(credits),
        "total_debits": len(debits),
        "total_transactions": len(cust_df),
        "monthly_credit_avg": float(credit_monthly.groupby('month').size().mean()) if len(credit_monthly) > 0 else 0,
        "monthly_credit_median": float(credit_monthly.groupby('month').size().median()) if len(credit_monthly) > 0 else 0,
        "monthly_debit_avg": float(debit_monthly.groupby('month').size().mean()) if len(debit_monthly) > 0 else 0,
        "monthly_debit_median": float(debit_monthly.groupby('month').size().median()) if len(debit_monthly) > 0 else 0,
        "credit_debit_ratio": len(credits) / len(debits) if len(debits) > 0 else 0
    }


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


def detect_anomalies(customer_id: int, threshold_std: float = 2.0) -> Dict[str, Any]:
    """Detect credit/debit spikes using standard deviation threshold."""
    df = get_transactions_df()
    cust_df = df[df['cust_id'] == customer_id]

    results = {
        "customer_id": customer_id,
        "threshold_std": threshold_std,
        "credit_spikes": [],
        "debit_spikes": [],
        "credit_spike_count": 0,
        "debit_spike_count": 0,
        "currency": "INR"
    }

    for indicator, key in [('C', 'credit_spikes'), ('D', 'debit_spikes')]:
        subset = cust_df[cust_df['dr_cr_indctor'] == indicator]
        if len(subset) > 2:
            mean = subset['tran_amt_in_ac'].mean()
            std = subset['tran_amt_in_ac'].std()
            threshold = mean + threshold_std * std

            spikes = subset[subset['tran_amt_in_ac'] > threshold]
            results[key] = spikes[['tran_date', 'tran_amt_in_ac', 'category_of_txn', 'tran_type']].to_dict('records')
            results[f"{key.replace('_spikes', '')}_spike_count"] = len(spikes)

            # Add statistics
            results[f"{key.replace('_spikes', '')}_mean"] = float(mean)
            results[f"{key.replace('_spikes', '')}_std"] = float(std)
            results[f"{key.replace('_spikes', '')}_threshold"] = float(threshold)

    return results


def get_income_stability(customer_id: int) -> Dict[str, Any]:
    """Analyze income stability and consistency."""
    df = get_transactions_df()
    credits = df[(df['cust_id'] == customer_id) & (df['dr_cr_indctor'] == 'C')]

    if len(credits) == 0:
        return {
            "customer_id": customer_id,
            "stability_score": 0,
            "income_sources": {},
            "salary_regularity": "no_data",
            "currency": "INR"
        }

    credits_copy = credits.copy()
    credits_copy['month'] = credits_copy['tran_date'].str[:7]

    # Income by source
    income_by_source = credits.groupby('category_of_txn')['tran_amt_in_ac'].sum().to_dict()

    # Monthly income variance
    monthly_income = credits_copy.groupby('month')['tran_amt_in_ac'].sum()
    income_std = monthly_income.std() if len(monthly_income) > 1 else 0
    income_mean = monthly_income.mean()

    # Coefficient of variation (lower = more stable)
    cv = (income_std / income_mean) if income_mean > 0 else 0

    # Stability score (0-100, higher = more stable)
    stability_score = max(0, min(100, 100 - (cv * 100)))

    # Salary regularity — canonical dual rule (registry salary role OR salary
    # narration keyword), matching the deterministic salary block.
    from tools.rules import is_salary_credit
    salary = credits[credits.apply(
        lambda r: is_salary_credit(r.get('category_of_txn_l2'), r.get('tran_partclr')),
        axis=1,
    )]
    salary_copy = salary.copy()
    salary_copy['month'] = salary_copy['tran_date'].str[:7]
    salary_months = salary_copy['month'].nunique()
    total_months = credits_copy['month'].nunique()

    if total_months > 0 and salary_months == total_months:
        salary_regularity = "regular"
    elif salary_months > 0:
        salary_regularity = "irregular"
    else:
        salary_regularity = "no_salary_detected"

    return {
        "customer_id": customer_id,
        "stability_score": round(stability_score, 2),
        "coefficient_of_variation": round(cv, 4),
        "income_sources": {k: float(v) for k, v in income_by_source.items()},
        "primary_income_source": max(income_by_source, key=income_by_source.get) if income_by_source else None,
        "salary_regularity": salary_regularity,
        "monthly_income_avg": float(income_mean),
        "monthly_income_std": float(income_std),
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

    # cust_df['month'] = cust_df['tran_date'].str[:7]
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


def generate_customer_report(customer_id: int) -> Dict[str, Any]:
    """Comprehensive customer report combining all metrics."""
    return {
        "customer_id": customer_id,
        "report_type": "comprehensive",
        "credit_statistics": get_credit_statistics(customer_id),
        "debit_statistics": get_debit_statistics(customer_id),
        "transaction_counts": get_transaction_counts(customer_id),
        "balance_trend": get_balance_trend(customer_id),
        "anomalies": detect_anomalies(customer_id),
        "income_stability": get_income_stability(customer_id),
        "cash_flow": get_cash_flow(customer_id),
        "top_spending": top_spending_categories(customer_id, top_n=5),
        "total_income": get_total_income(customer_id),
        "total_spending": debit_total(customer_id)
    }


def generate_lender_profile(customer_id: int) -> Dict[str, Any]:
    """Lender-focused creditworthiness summary for underwriting decisions."""
    # Get all underlying data
    credit_stats = get_credit_statistics(customer_id)
    debit_stats = get_debit_statistics(customer_id)
    txn_counts = get_transaction_counts(customer_id)
    balance = get_balance_trend(customer_id)
    anomalies = detect_anomalies(customer_id)
    income_stability = get_income_stability(customer_id)
    cash_flow = get_cash_flow(customer_id)
    total_income = get_total_income(customer_id)
    total_spending = debit_total(customer_id)
    top_categories = top_spending_categories(customer_id, top_n=5)

    # Calculate key ratios
    income = total_income['total_income']
    spending = total_spending['total_spending']
    savings_rate = (income - spending) / income if income > 0 else 0
    income_expense_ratio = income / spending if spending > 0 else 0

    # Risk scoring
    risk_factors = []

    # Savings rate risk
    if savings_rate < 0:
        risk_factors.append("negative_savings")
    elif savings_rate < 0.1:
        risk_factors.append("low_savings_rate")

    # Income stability risk
    if income_stability['stability_score'] < 50:
        risk_factors.append("unstable_income")

    # Anomaly risk
    if anomalies['credit_spike_count'] > 3:
        risk_factors.append("irregular_income_patterns")
    if anomalies['debit_spike_count'] > 5:
        risk_factors.append("irregular_spending_patterns")

    # Balance trend risk
    if balance['trend'] == 'decreasing':
        risk_factors.append("declining_balance")
    if balance['min_balance'] < 0:
        risk_factors.append("negative_balance_history")

    # Determine risk level
    if len(risk_factors) == 0:
        risk_level = "low_risk"
    elif len(risk_factors) <= 2:
        risk_level = "medium_risk"
    else:
        risk_level = "high_risk"

    # Credit score proxy (0-100)
    credit_score = 100
    credit_score -= len(risk_factors) * 15
    credit_score = max(0, min(100, credit_score + (savings_rate * 50) + (income_stability['stability_score'] / 2)))

    return {
        "customer_id": customer_id,
        "report_type": "lender_profile",

        # Key financial metrics
        "total_income": income,
        "total_spending": spending,
        "net_position": income - spending,
        "savings_rate": round(savings_rate, 4),
        "income_to_expense_ratio": round(income_expense_ratio, 2),

        # Income analysis
        "income_stability_score": income_stability['stability_score'],
        "primary_income_source": income_stability['primary_income_source'],
        "salary_regularity": income_stability['salary_regularity'],
        "monthly_avg_income": credit_stats['monthly_avg_amount'],
        "max_single_credit": credit_stats['max_credit'],

        # Spending analysis
        "monthly_avg_spending": debit_stats['monthly_avg_amount'],
        "top_expense_categories": list(top_categories['top_categories'].keys())[:3],
        "spending_volatility": anomalies['debit_spike_count'],

        # Balance & cash flow
        "current_balance": balance['final_balance'],
        "balance_trend": balance['trend'],
        "min_balance_observed": balance['min_balance'],
        "avg_net_cash_flow": cash_flow['avg_net_cash_flow'],

        # Transaction patterns
        "monthly_credit_txn_count": txn_counts['monthly_credit_avg'],
        "monthly_debit_txn_count": txn_counts['monthly_debit_avg'],

        # Risk assessment
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "credit_score_proxy": round(credit_score, 0),

        # Recommendation
        "lending_recommendation": "approve" if risk_level == "low_risk" else "review" if risk_level == "medium_risk" else "decline",

        "currency": "INR"
    }
