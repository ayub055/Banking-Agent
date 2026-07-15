"""Transaction filtering utilities for insight extraction."""

from typing import List, Dict, Any
from data.loader import get_transactions_df


def get_customer_transactions(customer_id: int) -> List[Dict[str, Any]]:
    """
    Get all transactions for a customer as list of dicts.

    Args:
        customer_id: Customer identifier

    Returns:
        List of transaction dictionaries
    """
    df = get_transactions_df()
    cust_df = df[df['cust_id'] == customer_id].copy()

    if cust_df.empty:
        return []

    columns = ['tran_date', 'tran_amt_in_ac', 'dr_cr_indctor', 'category_of_txn', 'tran_type']
    available_cols = [c for c in columns if c in cust_df.columns]
    cust_df = cust_df[available_cols]

    return cust_df.to_dict('records')


def filter_transactions(
    transactions: List[Dict[str, Any]],
    scope: str,
    max_records: int = 40
) -> List[Dict[str, Any]]:
    """
    Filter transactions based on scope to reduce LLM context size.

    Args:
        transactions: Full transaction list
        scope: Filter type - 'patterns', 'recurring_only', 'top_merchants', 'credits_only'
        max_records: Maximum records to return

    Returns:
        Filtered transaction list (never returns full history)
    """
    if not transactions:
        return []

    if scope == "patterns":
        sorted_txns = sorted(
            transactions,
            key=lambda x: x.get('tran_date', ''),
            reverse=True
        )
        return sorted_txns[:max_records]

    if scope == "recurring_only":
        from tools.category.registry import categories_with_role
        recurring_l2 = categories_with_role('recurring')
        recurring = [
            t for t in transactions
            if t.get('category_of_txn_l2') in recurring_l2
        ]
        return recurring[:max_records]

    if scope == "top_merchants":
        debits = [t for t in transactions if t.get('dr_cr_indctor') == 'D']
        sorted_debits = sorted(
            debits,
            key=lambda x: x.get('tran_amt_in_ac', 0),
            reverse=True
        )
        return sorted_debits[:max_records]

    if scope == "credits_only":
        credits = [t for t in transactions if t.get('dr_cr_indctor') == 'C']
        return credits[:max_records]

    return transactions[:max_records]


def format_transactions_for_llm(transactions: List[Dict[str, Any]]) -> str:
    """
    Format transactions as a string for LLM input.

    Args:
        transactions: List of transaction dicts

    Returns:
        Formatted string representation
    """
    if not transactions:
        return "No transactions available."

    lines = []
    for t in transactions:
        date = t.get('tran_date', 'N/A')
        dr_cr = t.get('dr_cr_indctor', 'N/A')
        amount = t.get('tran_amt_in_ac', 0)
        category = t.get('category_of_txn', 'N/A')
        tran_type = t.get('tran_type', 'N/A')

        line = f"{date} | {dr_cr} | {amount:.2f} | {category} | {tran_type}"
        lines.append(line)

    return "\n".join(lines)
