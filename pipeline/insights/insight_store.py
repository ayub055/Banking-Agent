"""Insight cache for transaction patterns."""

from typing import Dict, Tuple, Optional

from schemas.transaction_insights import TransactionInsights


_INSIGHT_CACHE: Dict[Tuple[int, str], TransactionInsights] = {}


def get_cached_insights(customer_id: int, scope: str) -> Optional[TransactionInsights]:
    """
    Retrieve cached insights for a customer and scope.

    Args:
        customer_id: Customer identifier
        scope: Analysis scope used

    Returns:
        Cached TransactionInsights or None if not cached
    """
    return _INSIGHT_CACHE.get((customer_id, scope))


def store_insights(customer_id: int, scope: str, insights: TransactionInsights) -> None:
    """
    Store insights in cache.

    Args:
        customer_id: Customer identifier
        scope: Analysis scope used
        insights: TransactionInsights to cache
    """
    _INSIGHT_CACHE[(customer_id, scope)] = insights


def clear_customer_cache(customer_id: int) -> None:
    """Clear all cached insights for a customer."""
    keys_to_remove = [k for k in _INSIGHT_CACHE if k[0] == customer_id]
    for key in keys_to_remove:
        del _INSIGHT_CACHE[key]


def clear_all_cache() -> None:
    """Clear entire insight cache."""
    _INSIGHT_CACHE.clear()


def get_cache_stats() -> Dict[str, int]:
    """Get cache statistics."""
    return {
        "total_entries": len(_INSIGHT_CACHE),
        "unique_customers": len(set(k[0] for k in _INSIGHT_CACHE))
    }
