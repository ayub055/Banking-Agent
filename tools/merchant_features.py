"""Merchant-level features for banking transaction analysis.

Computes per-merchant behavioral features from raw transaction data.
All functions are standalone callables that accept a list of transaction
dicts and return simple dicts/lists. No LLM calls — purely deterministic.

Reuses existing merchant extraction logic from utils/narration_utils.py
and fuzzy matching pattern from tools/transaction_fetcher.py.
"""

import statistics
from collections import defaultdict
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from utils.narration_utils import (
    extract_recipient_name,
    clean_narration,
    normalize_narration,
    are_similar,
)
from tools.rules import is_self_transfer

try:
    from fuzzywuzzy import fuzz
    _FUZZYWUZZY_AVAILABLE = True
except ImportError:
    _FUZZYWUZZY_AVAILABLE = False

# Same threshold as tools/transaction_fetcher.py
_SIMILARITY_THRESHOLD = 70


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _filter_transactions(
    transactions: List[Dict[str, Any]],
    direction: Optional[str] = None,
    exclude_self_transfers: bool = True,
) -> List[Dict[str, Any]]:
    """Filter transactions by direction and self-transfer flag."""
    result = []
    for txn in transactions:
        if exclude_self_transfers and is_self_transfer(txn):
            continue
        if direction and txn.get("dr_cr_indctor") != direction:
            continue
        result.append(txn)
    return result


def _group_by_merchant(
    transactions: List[Dict[str, Any]],
    direction: Optional[str] = None,
    exclude_self_transfers: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Group transactions by extracted merchant name using fuzzy matching.

    REUSES EXISTING LOGIC:
    - extract_recipient_name() from utils/narration_utils.py
    - clean_narration() from utils/narration_utils.py as fallback
    - normalize_narration() + fuzzywuzzy for similarity (same as transaction_fetcher.py)

    Returns:
        Dict mapping merchant_name -> list of txn dicts (each enriched
        with 'direction' from the original dr_cr_indctor).
    """
    filtered = _filter_transactions(transactions, direction, exclude_self_transfers)

    groups: Dict[str, List[Dict[str, Any]]] = {}
    group_keys: List[str] = []  # ordered list of canonical names

    for txn in filtered:
        narration = str(txn.get("tran_partclr", ""))
        merchant = extract_recipient_name(narration)
        if not merchant:
            merchant = clean_narration(narration)
        if not merchant:
            continue

        # Find matching group via fuzzy match
        matched_key = None
        for key in group_keys:
            if are_similar(merchant, key, _SIMILARITY_THRESHOLD):
                matched_key = key
                break

        enriched = {**txn, "_merchant": merchant}

        if matched_key:
            groups[matched_key].append(enriched)
        else:
            groups[merchant] = [enriched]
            group_keys.append(merchant)

    return groups


def _get_month(txn: Dict[str, Any]) -> str:
    """Extract YYYY-MM from tran_date."""
    return str(txn.get("tran_date", ""))[:7]


def _parse_date(txn: Dict[str, Any]) -> Optional[date]:
    """Parse tran_date string (YYYY-MM-DD) to date. Returns None on failure."""
    raw = txn.get("tran_date")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Public feature functions
# ---------------------------------------------------------------------------

def get_merchant_distinct_months(
    transactions: List[Dict[str, Any]],
    direction: Optional[str] = None,
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Number of distinct months each merchant appeared in.

    Returns:
        List of dicts with merchant, direction, distinct_months, months.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    result = []
    for merchant, txns in groups.items():
        months = sorted(set(_get_month(t) for t in txns if _get_month(t)))
        dirs = set(t.get("dr_cr_indctor", "") for t in txns)
        result.append({
            "merchant": merchant,
            "direction": "/".join(sorted(dirs)),
            "distinct_months": len(months),
            "months": months,
        })
    return result


def get_merchant_monthly_counts(
    transactions: List[Dict[str, Any]],
    direction: Optional[str] = None,
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Monthly transaction count per merchant.

    Returns:
        List of dicts with merchant, direction, monthly_counts, total_count.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    result = []
    for merchant, txns in groups.items():
        monthly: Dict[str, int] = defaultdict(int)
        for t in txns:
            m = _get_month(t)
            if m:
                monthly[m] += 1
        dirs = set(t.get("dr_cr_indctor", "") for t in txns)
        result.append({
            "merchant": merchant,
            "direction": "/".join(sorted(dirs)),
            "monthly_counts": dict(sorted(monthly.items())),
            "total_count": len(txns),
        })
    return result


def get_merchant_monthly_amount_stats(
    transactions: List[Dict[str, Any]],
    direction: Optional[str] = None,
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Monthly avg, median, max amount per merchant.

    Returns:
        List of dicts with merchant, direction, avg/median/max/total_amount.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    result = []
    for merchant, txns in groups.items():
        amounts = [float(t.get("tran_amt_in_ac", 0)) for t in txns]
        if not amounts:
            continue
        dirs = set(t.get("dr_cr_indctor", "") for t in txns)
        result.append({
            "merchant": merchant,
            "direction": "/".join(sorted(dirs)),
            "avg_amount": sum(amounts) / len(amounts),
            "median_amount": statistics.median(amounts),
            "max_amount": max(amounts),
            "total_amount": sum(amounts),
        })
    return result


def get_regular_merchants(
    transactions: List[Dict[str, Any]],
    min_months: int = 2,
    total_months: int = 6,
    direction: Optional[str] = None,
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Merchants appearing in at least min_months distinct months.

    Returns:
        List of dicts with merchant, direction, distinct_months, is_regular, avg_amount.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    result = []
    for merchant, txns in groups.items():
        months = set(_get_month(t) for t in txns if _get_month(t))
        if len(months) < min_months:
            continue
        amounts = [float(t.get("tran_amt_in_ac", 0)) for t in txns]
        dirs = set(t.get("dr_cr_indctor", "") for t in txns)
        result.append({
            "merchant": merchant,
            "direction": "/".join(sorted(dirs)),
            "distinct_months": len(months),
            "is_regular": True,
            "avg_amount": sum(amounts) / len(amounts) if amounts else 0,
        })
    return result


def get_anomaly_merchants(
    transactions: List[Dict[str, Any]],
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Merchants flagged as anomalous — one-time large transactions.

    Criterion: appeared exactly once, amount > 3x customer's median debit amount.

    Returns:
        List of dicts with merchant, direction, anomaly_reason, amount, count.
    """
    # Compute median debit amount across all transactions
    debit_amounts = [
        float(t.get("tran_amt_in_ac", 0))
        for t in transactions
        if t.get("dr_cr_indctor") == "D"
        and not (exclude_self_transfers and is_self_transfer(t))
    ]
    if not debit_amounts:
        return []
    median_debit = statistics.median(debit_amounts)
    threshold = median_debit * 3

    if groups is None:
        groups = _group_by_merchant(transactions, exclude_self_transfers=exclude_self_transfers)
    result = []
    for merchant, txns in groups.items():
        if len(txns) != 1:
            continue
        amt = float(txns[0].get("tran_amt_in_ac", 0))
        if amt > threshold:
            result.append({
                "merchant": merchant,
                "direction": txns[0].get("dr_cr_indctor", ""),
                "anomaly_reason": "one_time_large",
                "amount": amt,
                "count": 1,
            })
    return result


def get_merchant_concentration(
    transactions: List[Dict[str, Any]],
    direction: str = "D",
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Spending concentration across merchants.

    Returns:
        Dict with top_1_pct, top_3_pct, hhi, total_merchants.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    if not groups:
        return {"top_1_pct": 0, "top_3_pct": 0, "hhi": 0, "total_merchants": 0}

    totals = {m: sum(float(t.get("tran_amt_in_ac", 0)) for t in txns)
              for m, txns in groups.items()}
    grand_total = sum(totals.values())
    if grand_total <= 0:
        return {"top_1_pct": 0, "top_3_pct": 0, "hhi": 0, "total_merchants": len(groups)}

    sorted_totals = sorted(totals.values(), reverse=True)
    if not sorted_totals:
        return {"top_1_pct": 0, "top_3_pct": 0, "hhi": 0, "total_merchants": len(groups)}
    top_1_pct = (sorted_totals[0] / grand_total) * 100
    top_3_pct = (sum(sorted_totals[:3]) / grand_total) * 100

    # HHI: sum of squared market shares (each as percentage)
    hhi = sum((v / grand_total * 100) ** 2 for v in sorted_totals)

    return {
        "top_1_pct": round(top_1_pct, 1),
        "top_3_pct": round(top_3_pct, 1),
        "hhi": round(hhi, 1),
        "total_merchants": len(groups),
    }


def get_merchant_amount_trend(
    transactions: List[Dict[str, Any]],
    direction: Optional[str] = None,
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Amount trend per merchant (first-half vs second-half average).

    Only for merchants with transactions in 2+ distinct months.

    Returns:
        List of dicts with merchant, direction, trend, first_half_avg, second_half_avg.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    result = []
    for merchant, txns in groups.items():
        months = sorted(set(_get_month(t) for t in txns if _get_month(t)))
        if len(months) < 2:
            continue

        mid = len(months) // 2
        first_months = set(months[:mid])
        second_months = set(months[mid:])

        first_amounts = [float(t.get("tran_amt_in_ac", 0)) for t in txns
                         if _get_month(t) in first_months]
        second_amounts = [float(t.get("tran_amt_in_ac", 0)) for t in txns
                          if _get_month(t) in second_months]

        first_avg = sum(first_amounts) / len(first_amounts) if first_amounts else 0
        second_avg = sum(second_amounts) / len(second_amounts) if second_amounts else 0

        if abs(first_avg) < 1e-9:
            trend = "stable"
        elif second_avg / first_avg > 1.2:
            trend = "increasing"
        elif second_avg / first_avg < 0.8:
            trend = "decreasing"
        else:
            trend = "stable"

        dirs = set(t.get("dr_cr_indctor", "") for t in txns)
        result.append({
            "merchant": merchant,
            "direction": "/".join(sorted(dirs)),
            "trend": trend,
            "first_half_avg": round(first_avg, 2),
            "second_half_avg": round(second_avg, 2),
        })
    return result


def get_round_amount_merchants(
    transactions: List[Dict[str, Any]],
    direction: str = "D",
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Merchants where >80% of transactions are round amounts (divisible by 100).

    Returns:
        List of dicts with merchant, round_pct, count.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    result = []
    for merchant, txns in groups.items():
        amounts = [float(t.get("tran_amt_in_ac", 0)) for t in txns]
        if not amounts:
            continue
        round_count = sum(1 for a in amounts if a > 0 and abs(round(a) % 100) < 0.01)
        round_pct = (round_count / len(amounts)) * 100
        if round_pct > 80:
            result.append({
                "merchant": merchant,
                "round_pct": round(round_pct, 1),
                "count": len(amounts),
            })
    return result


def get_new_merchant_ratio(
    transactions: List[Dict[str, Any]],
    direction: str = "D",
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Ratio of merchants that first appeared in the last month of data.

    Returns:
        Dict with new_merchant_count, total_merchant_count, ratio, new_merchants.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    if not groups:
        return {"new_merchant_count": 0, "total_merchant_count": 0, "ratio": 0, "new_merchants": []}

    # Find last month across all transactions
    all_months = set()
    for txns in groups.values():
        for t in txns:
            m = _get_month(t)
            if m:
                all_months.add(m)
    if not all_months:
        return {"new_merchant_count": 0, "total_merchant_count": len(groups), "ratio": 0, "new_merchants": []}

    last_month = max(all_months)

    new_merchants = []
    for merchant, txns in groups.items():
        merchant_months = set(_get_month(t) for t in txns if _get_month(t))
        if merchant_months == {last_month}:
            total = sum(float(t.get("tran_amt_in_ac", 0)) for t in txns)
            new_merchants.append({"name": merchant, "amount": total})

    return {
        "new_merchant_count": len(new_merchants),
        "total_merchant_count": len(groups),
        "ratio": round(len(new_merchants) / len(groups), 2) if groups else 0,
        "new_merchants": new_merchants,
    }


def get_emerging_merchants(
    transactions: List[Dict[str, Any]],
    recent_months: int = 3,
    direction: str = "D",
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Merchants appearing in recent N months but absent in the preceding N months.

    Returns:
        Dict with emerging_merchants list, recent_window, prior_window.
    """
    empty = {"emerging_merchants": [], "recent_window": "", "prior_window": ""}
    if groups is None:
        groups = _group_by_merchant(transactions, direction, exclude_self_transfers)
    if not groups:
        return empty

    all_months: set = set()
    for txns in groups.values():
        for t in txns:
            m = _get_month(t)
            if m:
                all_months.add(m)

    sorted_months = sorted(all_months)
    if len(sorted_months) < recent_months * 2:
        return empty

    recent_set = set(sorted_months[-recent_months:])
    prior_set = set(sorted_months[-recent_months * 2:-recent_months])

    emerging = []
    for merchant, txns in groups.items():
        merchant_months = set(_get_month(t) for t in txns if _get_month(t))
        if merchant_months & recent_set and not merchant_months & prior_set:
            count = sum(1 for t in txns if _get_month(t) in recent_set)
            total = sum(float(t.get("tran_amt_in_ac", 0)) for t in txns if _get_month(t) in recent_set)
            emerging.append({"name": merchant, "count": count, "total_amount": round(total, 2)})

    emerging.sort(key=lambda x: x["total_amount"], reverse=True)

    return {
        "emerging_merchants": emerging,
        "recent_window": f"{min(recent_set)} to {max(recent_set)}",
        "prior_window": f"{min(prior_set)} to {max(prior_set)}",
    }


def get_favourite_merchants_ipt(
    transactions: List[Dict[str, Any]],
    top_n: Optional[int] = None,
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Top-N merchants by engagement, enriched with Inter-Purchase Time.

    Computes separately for debit and credit directions.  IPT is the
    average number of days between consecutive transactions with the
    same merchant — a lower IPT indicates a stronger, more regular
    relationship.

    Returns:
        Dict with "debit" and "credit" keys, each a list of dicts with
        merchant, count, total_amount, avg_ipt_days, min_ipt_days, max_ipt_days.
    """
    import config.thresholds as T

    if top_n is None:
        top_n = T.MERCHANT_FAVOURITE_TOP_N

    if groups is None:
        groups = _group_by_merchant(transactions, direction=None,
                                    exclude_self_transfers=exclude_self_transfers)

    result: Dict[str, List[Dict[str, Any]]] = {"debit": [], "credit": []}

    # Split each merchant's txns by direction
    for merchant, txns in groups.items():
        by_dir: Dict[str, List[Dict[str, Any]]] = {"D": [], "C": []}
        for t in txns:
            d = t.get("dr_cr_indctor", "")
            if d in by_dir:
                by_dir[d].append(t)

        for dir_code, dir_key in [("D", "debit"), ("C", "credit")]:
            dir_txns = by_dir[dir_code]
            if not dir_txns:
                continue

            count = len(dir_txns)
            total_amount = sum(float(t.get("tran_amt_in_ac", 0)) for t in dir_txns)

            # Compute IPT from sorted dates
            dates = sorted(d for t in dir_txns if (d := _parse_date(t)) is not None)
            avg_ipt = None
            min_ipt = None
            max_ipt = None
            if len(dates) >= 2:
                gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
                gaps = [g for g in gaps if g > 0]  # skip same-day duplicates
                if gaps:
                    avg_ipt = round(sum(gaps) / len(gaps), 1)
                    min_ipt = min(gaps)
                    max_ipt = max(gaps)

            score = count * total_amount
            result[dir_key].append({
                "merchant": merchant,
                "count": count,
                "total_amount": round(total_amount, 2),
                "avg_ipt_days": avg_ipt,
                "min_ipt_days": min_ipt,
                "max_ipt_days": max_ipt,
                "_score": score,
            })

    # Sort by score and take top_n for each direction
    for key in ("debit", "credit"):
        result[key].sort(key=lambda x: x["_score"], reverse=True)
        result[key] = result[key][:top_n]
        for entry in result[key]:
            del entry["_score"]

    return result


def get_significant_merchants(
    transactions: List[Dict[str, Any]],
    threshold: Optional[float] = None,
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Merchants whose debit or credit volume >= threshold % of total flow.

    Identifies counterparties that dominate the customer's transaction
    profile — useful for understanding financial dependencies.

    Returns:
        List of dicts with merchant, debit_amount, credit_amount,
        debit_pct, credit_pct.  Sorted by max(debit_pct, credit_pct) desc.
    """
    import config.thresholds as T

    if threshold is None:
        threshold = T.MERCHANT_SIGNIFICANT_PCT

    if groups is None:
        groups = _group_by_merchant(transactions, direction=None,
                                    exclude_self_transfers=exclude_self_transfers)
    if not groups:
        return []

    # Compute totals across all merchants
    total_debits = 0.0
    total_credits = 0.0
    merchant_data: List[Dict[str, Any]] = []

    for merchant, txns in groups.items():
        d_amt = sum(float(t.get("tran_amt_in_ac", 0))
                    for t in txns if t.get("dr_cr_indctor") == "D")
        c_amt = sum(float(t.get("tran_amt_in_ac", 0))
                    for t in txns if t.get("dr_cr_indctor") == "C")
        total_debits += d_amt
        total_credits += c_amt
        merchant_data.append({
            "merchant": merchant,
            "debit_amount": round(d_amt, 2),
            "credit_amount": round(c_amt, 2),
        })

    result = []
    for md in merchant_data:
        d_pct = md["debit_amount"] / total_debits if total_debits > 0 else 0
        c_pct = md["credit_amount"] / total_credits if total_credits > 0 else 0
        if d_pct >= threshold or c_pct >= threshold:
            md["debit_pct"] = round(d_pct, 4)
            md["credit_pct"] = round(c_pct, 4)
            result.append(md)

    result.sort(key=lambda x: max(x["debit_pct"], x["credit_pct"]), reverse=True)
    return result


def get_bidirectional_merchants(
    transactions: List[Dict[str, Any]],
    exclude_self_transfers: bool = True,
    groups: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Merchants with both credit and debit transactions on different dates.

    Reveals business relationships or circular flows where money moves
    in both directions with the same counterparty.

    Filters out same-day-same-amount pairs (strong self-transfer /
    routing signal) and narrations containing "OWN" (own-account
    transfers that may not be flagged via the self_transfer column).

    Enriches each result with date ranges and a flow_pattern:
      - "received_then_paid" — earliest credit precedes earliest debit
        (dependency: customer receives funds then pays the merchant)
      - "paid_then_received" — earliest debit precedes earliest credit
        (reimbursement or refund pattern)

    Returns:
        List of dicts sorted by abs(net_flow) descending.
    """
    if groups is None:
        groups = _group_by_merchant(transactions, direction=None,
                                    exclude_self_transfers=exclude_self_transfers)
    result = []

    for merchant, txns in groups.items():
        # Skip own-account transfers that the self_transfer flag missed
        if any(kw in str(t.get("tran_partclr", "")).upper()
               for t in txns for kw in ("FROM OWN", "TO OWN")):
            continue

        credits = [t for t in txns if t.get("dr_cr_indctor") == "C"]
        debits = [t for t in txns if t.get("dr_cr_indctor") == "D"]

        if not credits or not debits:
            continue

        # Remove same-day-same-amount pairs (routing pattern)
        credit_dates_amts = {
            (str(t.get("tran_date", ""))[:10], float(t.get("tran_amt_in_ac", 0)))
            for t in credits
        }
        debit_dates_amts = {
            (str(t.get("tran_date", ""))[:10], float(t.get("tran_amt_in_ac", 0)))
            for t in debits
        }
        same_day_pairs = credit_dates_amts & debit_dates_amts
        if same_day_pairs:
            # Remove matched pairs from both sides
            credits = [t for t in credits
                       if (str(t.get("tran_date", ""))[:10],
                           float(t.get("tran_amt_in_ac", 0))) not in same_day_pairs]
            debits = [t for t in debits
                      if (str(t.get("tran_date", ""))[:10],
                          float(t.get("tran_amt_in_ac", 0))) not in same_day_pairs]

        # After filtering, must still have both directions
        if not credits or not debits:
            continue

        total_credit = sum(float(t.get("tran_amt_in_ac", 0)) for t in credits)
        total_debit = sum(float(t.get("tran_amt_in_ac", 0)) for t in debits)

        # Date ranges
        credit_dates = sorted(d for t in credits if (d := _parse_date(t)) is not None)
        debit_dates = sorted(d for t in debits if (d := _parse_date(t)) is not None)

        # Flow pattern: who transacted first?
        flow_pattern = "unknown"
        if credit_dates and debit_dates:
            if credit_dates[0] <= debit_dates[0]:
                flow_pattern = "received_then_paid"
            else:
                flow_pattern = "paid_then_received"

        result.append({
            "merchant": merchant,
            "total_credit": round(total_credit, 2),
            "total_debit": round(total_debit, 2),
            "net_flow": round(total_credit - total_debit, 2),
            "credit_count": len(credits),
            "debit_count": len(debits),
            "flow_pattern": flow_pattern,
            "first_credit": str(credit_dates[0]) if credit_dates else None,
            "last_credit": str(credit_dates[-1]) if credit_dates else None,
            "first_debit": str(debit_dates[0]) if debit_dates else None,
            "last_debit": str(debit_dates[-1]) if debit_dates else None,
        })

    result.sort(key=lambda x: abs(x["net_flow"]), reverse=True)
    return result


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def compute_all_merchant_features(
    customer_id: int,
    exclude_self_transfers: bool = True,
) -> Dict[str, Any]:
    """Compute all merchant features for a customer.

    Loads transactions via load_transactions(), runs the fuzzy merchant
    grouping ONCE per direction shape (all-directions and debit-only), then
    passes the precomputed groups into each feature function instead of
    re-grouping 13 times.

    Args:
        customer_id: Customer identifier.
        exclude_self_transfers: Exclude self-transfer transactions (default True).

    Returns:
        Dict with keys for each feature category.
    """
    from data.loader import load_transactions

    df = load_transactions()
    cust_df = df[df["cust_id"] == customer_id]
    if len(cust_df) == 0:
        return {}

    transactions = cust_df.to_dict("records")

    # Two grouping shapes cover every feature: direction=None (both sides)
    # and direction="D" (debit-only). Grouping is direction-sensitive (the
    # greedy fuzzy pass depends on which txns are present), so each shape is
    # computed exactly as the feature functions would themselves.
    groups_all = _group_by_merchant(transactions, None, exclude_self_transfers)
    groups_debit = _group_by_merchant(transactions, "D", exclude_self_transfers)

    return {
        "distinct_months": get_merchant_distinct_months(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
        "monthly_counts": get_merchant_monthly_counts(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
        "amount_stats": get_merchant_monthly_amount_stats(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
        "regular_merchants": get_regular_merchants(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
        "anomaly_merchants": get_anomaly_merchants(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
        "concentration": get_merchant_concentration(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_debit),
        "amount_trends": get_merchant_amount_trend(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
        "round_amount_merchants": get_round_amount_merchants(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_debit),
        "new_merchant_ratio": get_new_merchant_ratio(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_debit),
        "emerging_merchants": get_emerging_merchants(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_debit),
        "favourite_merchants_ipt": get_favourite_merchants_ipt(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
        "significant_merchants": get_significant_merchants(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
        "bidirectional_merchants": get_bidirectional_merchants(transactions, exclude_self_transfers=exclude_self_transfers, groups=groups_all),
    }
