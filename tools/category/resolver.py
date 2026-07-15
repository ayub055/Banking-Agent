"""Category presence resolver - deterministic category detection.

This module resolves whether a customer has transactions in a specific category
using keyword matching, fuzzy matching, and category-specific rules.

NO LLM calls - purely deterministic logic.
"""

from typing import Dict, Any, List, Optional

from data.loader import get_transactions_df
from config.category_loader import (
    get_category_config,
    resolve_category_alias,
    get_fallback_config,
    CategoryConfig
)
from schemas.category_presence import CategoryPresenceResult, SupportingTransaction

try:
    from fuzzywuzzy import fuzz
    FUZZYWUZZY_AVAILABLE = True
except ImportError:
    FUZZYWUZZY_AVAILABLE = False


def resolve_category_presence(
    customer_id: int,
    category: str,
    max_supporting_txns: int = 10
) -> Dict[str, Any]:
    """
    Resolve whether a customer has transactions for a specific category.

    This is the main entry point for category presence lookup.

    Args:
        customer_id: Customer identifier
        category: Category to check (will be resolved via alias if needed)
        max_supporting_txns: Maximum supporting transactions to include

    Returns:
        Dictionary matching CategoryPresenceResult schema
    """
    # Step 1: Resolve category alias to canonical key
    resolved_category = resolve_category_alias(category)
    category_key = resolved_category if resolved_category else category

    # Step 2: Get category configuration
    config = get_category_config(category_key)

    # Step 3: Get customer transactions
    df = get_transactions_df()
    cust_df = df[df['cust_id'] == customer_id].copy()

    if len(cust_df) == 0:
        return CategoryPresenceResult(
            customer_id=customer_id,
            category=category,
            present=False,
            category_config_used=category_key
        ).to_dict()

    # Step 4: Apply direction filter if specified
    direction_filter = None
    if config and config.direction:
        direction_filter = config.direction
        cust_df = cust_df[cust_df['dr_cr_indctor'] == config.direction]

    # Step 5: Find matching transactions
    matching_txns = _find_matching_transactions(cust_df, config, category)

    # Step 6: Calculate results
    min_count = config.min_count if config else 1
    present = len(matching_txns) >= min_count

    total_amount = sum(t['amount'] for t in matching_txns)

    # Step 7: Build supporting transactions (limited)
    supporting = [
        SupportingTransaction(
            date=str(t['date']),
            amount=t['amount'],
            narration=t['narration'][:100] if t['narration'] else '',  # Truncate long narrations
            transaction_type=t.get('tran_type', 'Unknown'),
            direction=t['direction']
        )
        for t in matching_txns[:max_supporting_txns]
    ]

    # Step 8: Collect matched keywords for debugging
    matched_keywords = list(set(
        t.get('matched_keyword', '')
        for t in matching_txns
        if t.get('matched_keyword')
    ))

    result = CategoryPresenceResult(
        customer_id=customer_id,
        category=category,
        present=present,
        total_amount=total_amount,
        transaction_count=len(matching_txns),
        supporting_transactions=supporting,
        direction_filter=direction_filter,
        matched_keywords=matched_keywords,
        category_config_used=category_key
    )

    return result.to_dict()


def _find_matching_transactions(
    cust_df,
    config: Optional[CategoryConfig],
    raw_category: str
) -> List[Dict[str, Any]]:
    """
    Find transactions matching category criteria.

    Uses matching strategies in order of priority:
    0. Direct column match - category_of_txn matches raw query (case-insensitive)
    1. YAML config category_matches - exact match against configured values
    2. Keyword match in narration from YAML config
    3. Fuzzy match (if enabled and fuzzywuzzy available)

    Strategy 0 prioritizes data column values over categories.yaml fallback.
    """
    matches = []

    for _, row in cust_df.iterrows():
        # Convert row to dict for easier access
        row_dict = row.to_dict()

        txn_category = str(row_dict.get('category_of_txn', '')).upper()
        narration = str(row_dict.get('tran_partclr', '')).lower()

        matched = False
        matched_keyword = None

        # Strategy 0: Direct column match - check if category_of_txn matches raw query
        # This prioritizes actual data column values before using categories.yaml
        if txn_category and raw_category:
            if txn_category.lower() == raw_category.lower():
                matched = True
                matched_keyword = f"direct:{txn_category}"

        # Strategy 1: YAML config category_matches - exact match against configured values
        if not matched and config and config.category_matches:
            if txn_category in [c.upper() for c in config.category_matches]:
                matched = True
                matched_keyword = f"config:{txn_category}"

        # Strategy 2: Keyword match in narration from YAML config
        if not matched and config and config.keywords:
            for keyword in config.keywords:
                if keyword.lower() in narration:
                    matched = True
                    matched_keyword = keyword
                    break

        # Strategy 3: Fallback - fuzzy match on raw category name
        if not matched:
            fallback = get_fallback_config()
            if fallback.get('use_fuzzy_match', True):
                threshold = fallback.get('fuzzy_threshold', 70)
                if _fuzzy_match_narration(narration, raw_category, threshold):
                    matched = True
                    matched_keyword = f"fuzzy:{raw_category}"

        if matched:
            matches.append({
                'date': str(row_dict.get('tran_date', '')),
                'amount': float(row_dict.get('tran_amt_in_ac', 0)),
                'narration': str(row_dict.get('tran_partclr', '')),
                'tran_type': str(row_dict.get('tran_type', 'Unknown')),
                'direction': str(row_dict.get('dr_cr_indctor', 'D')),
                'matched_keyword': matched_keyword
            })

    # Sort by amount descending for most significant first
    matches.sort(key=lambda x: x['amount'], reverse=True)

    return matches


def _fuzzy_match_narration(narration: str, category: str, threshold: int) -> bool:
    """Check if narration fuzzy matches category name."""
    if not FUZZYWUZZY_AVAILABLE:
        # Fallback to simple substring check
        return category.lower() in narration.lower()

    # Use token set ratio for flexible matching
    score = fuzz.token_set_ratio(narration, category)
    return score >= threshold


def category_presence_lookup(
    customer_id: int,
    category: str
) -> Dict[str, Any]:
    """
    Tool function: Check if customer has transactions for a category.

    This is the tool function registered with the executor.

    Args:
        customer_id: Customer identifier
        category: Category to check

    Returns:
        Category presence result dictionary
    """
    return resolve_category_presence(customer_id, category)
