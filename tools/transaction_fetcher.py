"""Transaction fetching and summarization with fuzzy matching.

This module provides deterministic transaction analysis:
- Salary/income detection
- Similar transaction grouping using fuzzywuzzy
- Structured output via Pydantic schemas

NO LLM calls - purely deterministic logic.
"""

import math
from typing import List, Dict, Any, Optional
from collections import defaultdict

from data.loader import get_transactions_df
from schemas.transaction_summary import (
    SalarySummary,
    HighFrequencyTransaction,
    TransactionSummary
)
from utils.narration_utils import (
    extract_recipient_name,
    clean_narration,
    are_similar,
)

try:
    from fuzzywuzzy import fuzz
    FUZZYWUZZY_AVAILABLE = True
except ImportError:
    FUZZYWUZZY_AVAILABLE = False
    print("Warning: fuzzywuzzy not installed. Install with: pip install fuzzywuzzy python-Levenshtein")


# Configuration
SIMILARITY_THRESHOLD = 70  # Minimum similarity for grouping
MIN_GROUP_SIZE = 1  # Minimum transactions to form a group (was 3 — lowered so rent/EMI with 1-2 txns appear)
MIN_SALARY_COUNT = 2  # Minimum salary transactions to detect


def fetch_transaction_summary(customer_id: int) -> TransactionSummary:
    """
    Fetch and summarize transactions for a customer.
    This is the main entry point for the transaction summarization subsystem.
    Args: customer_id: Customer identifier
    Returns: TransactionSummary with salary info and high-frequency transaction groups
    """
    df = get_transactions_df()
    cust_df = df[df['cust_id'] == customer_id]

    if len(cust_df) == 0:
        return TransactionSummary(customer_id=customer_id, total_transactions_analyzed=0)

    # Convert to list of dicts for processing
    transactions = cust_df.to_dict('records')

    # Detect salary
    salary_summary = _detect_salary(transactions)

    # Group similar transactions
    high_freq_txns = _group_similar_transactions(transactions)

    return TransactionSummary(
        customer_id=customer_id,
        salary_summary=salary_summary,
        high_frequency_transactions=high_freq_txns,
        total_transactions_analyzed=len(transactions)
    )


def _detect_salary(transactions: List[Dict[str, Any]]) -> Optional[SalarySummary]:
    """
    Detect salary/income transactions deterministically.

    Rules:
    - Filter credit transactions (dr_cr_indctor == 'C')
    - Match by category == 'SALARY' OR salary keywords in narration
    - Require at least MIN_SALARY_COUNT occurrences

    Args:
        transactions: List of transaction dicts

    Returns:
        SalarySummary if salary detected, None otherwise
    """
    salary_txns = []

    for txn in transactions:
        # Only credits
        if txn.get('dr_cr_indctor') != 'C':
            continue

        # Canonical salary rule (registry role OR narration keyword).
        from tools.rules import is_salary_credit
        l2 = txn.get('category_of_txn_l2', '')
        narration = str(txn.get('tran_partclr', ''))

        if is_salary_credit(l2, narration):
            salary_txns.append({
                'amount': float(txn.get('tran_amt_in_ac', 0)),
                'narration': narration,
                'date': txn.get('tran_date', '')
            })

    if len(salary_txns) < MIN_SALARY_COUNT:
        return None

    amounts = [t['amount'] for t in salary_txns]
    narrations = [t['narration'] for t in salary_txns]

    return SalarySummary(
        average_amount=sum(amounts) / len(amounts),
        frequency="monthly",
        narrations=narrations,
        transaction_count=len(salary_txns),
        total_amount=sum(amounts)
    )


def _compute_score(count: int, total_amount: float) -> float:
    """Hybrid ranking score that balances frequency and amount.

    Uses sqrt(count) * log10(1 + total_amount) so that:
    - 2x ₹50K rent  → 1.41 * 4.70 = 6.63
    - 5x ₹50 UPI    → 2.24 * 2.40 = 5.37
    - Large infrequent payments rank above many tiny ones.
    """
    if count <= 0 or total_amount <= 0:
        return 0.0
    return math.sqrt(count) * math.log10(1 + total_amount)


def _group_similar_transactions(
    transactions: List[Dict[str, Any]]
) -> List[HighFrequencyTransaction]:
    """
    Group similar transactions using fuzzy matching.

    Algorithm:
    1. Separate debits and credits (exclude salary credits)
    2. Sort each set by recipient name for deterministic grouping
    3. Extract recipient names from narrations (fallback to cleaned narration)
    4. Fuzzy-group by recipient name
    5. Rank by hybrid score (sqrt(count) * log10(1 + total_amount))

    Args:
        transactions: List of transaction dicts

    Returns:
        List of HighFrequencyTransaction groups sorted by score descending
    """
    if not FUZZYWUZZY_AVAILABLE:
        return _group_by_exact_match(transactions)

    # Separate debits and credits
    debits = [t for t in transactions if t.get('dr_cr_indctor') == 'D']
    credits = [t for t in transactions if t.get('dr_cr_indctor') == 'C']

    # Process debits (spending patterns)
    debit_groups = _fuzzy_group_transactions(debits, 'D')

    # Process credits (income patterns) - excluding salary
    from tools.rules import is_salary_credit
    non_salary_credits = [
        t for t in credits
        if not is_salary_credit(t.get('category_of_txn_l2', ''), t.get('tran_partclr', ''))
    ]
    credit_groups = _fuzzy_group_transactions(non_salary_credits, 'C')

    # Combine and sort by hybrid score (not count)
    all_groups = debit_groups + credit_groups
    all_groups.sort(key=lambda x: x.score, reverse=True)

    return all_groups


def _fuzzy_group_transactions(
    transactions: List[Dict[str, Any]],
    txn_type: str
) -> List[HighFrequencyTransaction]:
    """
    Group transactions by fuzzy matching on recipient names.

    Transactions are sorted by recipient name before grouping to ensure
    deterministic results regardless of input order.

    Args:
        transactions: List of transactions to group
        txn_type: "D" or "C"

    Returns:
        List of transaction groups (filtered by MIN_GROUP_SIZE, scored)
    """
    # Pre-extract recipients and sort for deterministic grouping
    enriched = []
    for txn in transactions:
        narration = str(txn.get('tran_partclr', ''))
        amount = float(txn.get('tran_amt_in_ac', 0))
        recipient = extract_recipient_name(narration)

        if not recipient:
            # Fallback: cleaned full narration (readable, title-cased)
            recipient = clean_narration(narration)

        if not recipient:
            continue

        enriched.append((recipient, narration, amount))

    # Sort by recipient name for deterministic group formation
    enriched.sort(key=lambda x: x[0].lower())

    groups: List[Dict] = []

    for recipient, narration, amount in enriched:
        # Find matching group
        matched_group = None
        for group in groups:
            rep_name = group['representative']
            if are_similar(recipient, rep_name, SIMILARITY_THRESHOLD):
                matched_group = group
                break

        if matched_group:
            matched_group['narrations'].append(narration)
            matched_group['amounts'].append(amount)
            matched_group['recipients'].add(recipient)
        else:
            # Create new group
            groups.append({
                'representative': recipient,
                'narrations': [narration],
                'amounts': [amount],
                'recipients': {recipient}
            })

    # Convert to HighFrequencyTransaction, filtering by MIN_GROUP_SIZE
    result = []
    for group in groups:
        if len(group['narrations']) >= MIN_GROUP_SIZE:
            total = sum(group['amounts'])
            count = len(group['narrations'])
            score = _compute_score(count, total)
            result.append(HighFrequencyTransaction(
                representative_narration=group['representative'],
                similar_narrations=list(set(group['narrations'])),
                count=count,
                total_amount=total,
                average_amount=total / count if count > 0 else 0,
                transaction_type=txn_type,
                score=score
            ))

    return result


def _group_by_exact_match(
    transactions: List[Dict[str, Any]]
) -> List[HighFrequencyTransaction]:
    """
    Fallback grouping by exact recipient match (when fuzzywuzzy unavailable).

    Args:
        transactions: List of transactions

    Returns:
        List of transaction groups
    """
    groups = defaultdict(lambda: {'narrations': [], 'amounts': [], 'type': 'D'})

    for txn in transactions:
        narration = str(txn.get('tran_partclr', ''))
        amount = float(txn.get('tran_amt_in_ac', 0))
        txn_type = txn.get('dr_cr_indctor', 'D')
        recipient = extract_recipient_name(narration)

        if not recipient:
            recipient = clean_narration(narration)

        if not recipient:
            continue

        key = recipient.upper()
        groups[key]['narrations'].append(narration)
        groups[key]['amounts'].append(amount)
        groups[key]['type'] = txn_type

    result = []
    for name, data in groups.items():
        if len(data['narrations']) >= MIN_GROUP_SIZE:
            total = sum(data['amounts'])
            count = len(data['narrations'])
            score = _compute_score(count, total)
            result.append(HighFrequencyTransaction(
                representative_narration=name,
                similar_narrations=list(set(data['narrations'])),
                count=count,
                total_amount=total,
                average_amount=total / count if count > 0 else 0,
                transaction_type=data['type'],
                score=score
            ))

    result.sort(key=lambda x: x.score, reverse=True)
    return result
