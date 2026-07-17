"""
Data loading and management module.
Handles all data access in one place.
"""

import logging

import pandas as pd
from typing import Optional, Dict, Any

from config.settings import (
    CSV_DELIMITER,
    TRANSACTIONS_FILE,
    RG_SAL_FILE,
)
logger = logging.getLogger(__name__)

# Module-level cache for the dataframe
_transactions_df: Optional[pd.DataFrame] = None


def _normalise_categories(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise category_of_txn_l2 to canonical values and derive
    category_of_txn (L1) from it via the registry.

    Rules:
      - L2 column is authoritative; raw values are mapped to canonical L2 via
        the registry's alias index.
      - L1 column is always overwritten with l1_of(L2).
      - Unknown L2 values fall through to L1='Transfer' (SQL ELSE behaviour);
        a warning is logged once per load with the offending raw values.
    """
    if "category_of_txn_l2" not in df.columns:
        # Older CSVs without L2 — leave L1 as-is.
        return df

    # Imported lazily to avoid circular import (tools/__init__ imports analytics
    # which imports data.loader).
    from tools.category.registry import l1_of, l2_canonical, UNKNOWN_L1

    raw_l2 = df["category_of_txn_l2"]
    canonical_l2 = raw_l2.apply(lambda v: l2_canonical(v) or "")
    df["category_of_txn_l2"] = canonical_l2
    df["category_of_txn"] = canonical_l2.apply(l1_of)

    unknown_raw = sorted({
        str(v).strip()
        for orig, canon in zip(raw_l2, canonical_l2)
        if (str(orig).strip() and str(orig).strip().lower() not in {"nan", "null", "none"})
        and not canon
    })
    if unknown_raw:
        logger.warning(
            "Unknown L2 categories normalised to %s: %s",
            UNKNOWN_L1, unknown_raw,
        )
    return df


def load_transactions(force_reload: bool = False) -> pd.DataFrame:
    """
    Load transaction data from CSV.

    Args:
        force_reload: If True, reload from disk even if cached

    Returns:
        DataFrame with transaction data
    """
    global _transactions_df

    if _transactions_df is None or force_reload:
        _transactions_df = pd.read_csv(TRANSACTIONS_FILE, sep=CSV_DELIMITER, index_col=False)
        _transactions_df = _normalise_categories(_transactions_df)
        print(f"Loaded {len(_transactions_df)} transactions from {TRANSACTIONS_FILE}")

    return _transactions_df


def _compute_rg_income(customer_id: int) -> Optional[pd.DataFrame]:
    """Run the RG income extractor live over the loaded transactions for one
    customer and return its aggregated per-source output.

    Replaces the former read of the precomputed rg_income_strings.csv. The
    extractor is imported lazily to avoid the tools <-> data.loader circular
    import (same pattern as _normalise_categories). The transaction-level
    "strings" behind each source are logged for now; nothing consumes them yet.
    """
    from tools.salary_extractors.rg_income import RGIncomeExtractor

    ext = RGIncomeExtractor()
    try:
        out = ext.extract(df=load_transactions(), cust_id_filter=[str(customer_id)])
        if out is not None and len(out) > 0:
            logger.info(
                "RG income strings for %s:\n%s",
                customer_id, ext.get_income_strings().to_string(),
            )
        return out
    finally:
        ext.close()


def load_rg_salary_data(customer_id: int) -> Dict[str, Any]:
    """
    Load internal salary algorithm outputs for a customer.

    Reads rg_sal_strings.csv for the primary salary, and computes the
    multi-source total income (rg_income) live from the transactions via the
    RG income extractor. Returns structured data for template rendering.

    Args:
        customer_id: Customer identifier (CRN)

    Returns:
        Dict with optional 'rg_sal' and 'rg_income' sub-dicts.
        Returns {} if the customer has no salary/income data.
    """
    result: Dict[str, Any] = {}

    # --- Primary salary (rg_sal) ---
    try:
        sal_df = pd.read_csv(RG_SAL_FILE, sep=CSV_DELIMITER, index_col=False)
        cust_sal = sal_df[sal_df['crn'] == customer_id].copy()

        if len(cust_sal) > 0:
            salary_amount = float(cust_sal['rg_sal'].iloc[0])
            merchant = str(cust_sal['merchant'].iloc[0]).title()
            method = str(cust_sal['chosen_method'].iloc[0])
            pension_flag = int(cust_sal['pension_pay_flag'].iloc[0])

            cust_sal_sorted = cust_sal.sort_values('tran_date', ascending=False)
            transactions = [
                {
                    'date': str(row['tran_date']),
                    'narration': str(row['tran_partclr']),
                    'amount': float(row['tran_amt_in_ac']),
                }
                for _, row in cust_sal_sorted.iterrows()
            ]

            n = len(transactions)
            observation = (
                f"Estimated monthly salary of INR {salary_amount:,.0f} from {merchant}, "
                f"identified across {n} month{'s' if n != 1 else ''}."
            )

            result['rg_sal'] = {
                'salary_amount': salary_amount,
                'merchant': merchant,
                'method': method,
                'pension_flag': pension_flag,
                'transaction_count': n,
                'transactions': transactions,
                'observation': observation,
            }
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # --- Total income across all sources (rg_income) ---
    # Computed live from the transactions by the RG income extractor; there is
    # no precomputed file to read anymore.
    try:
        inc_out = _compute_rg_income(customer_id)

        if inc_out is not None and len(inc_out) > 0:
            total_income = float(inc_out['total_income'].iloc[0])

            # One income source per cluster; src_income is constant within a
            # cluster, so keep a single representative row per cluster_id.
            clusters = inc_out.drop_duplicates(subset='cluster_id')
            sources = []
            for _, row in clusters.iterrows():
                merchant_name = str(row['merchant']).title()
                months = [m for m in str(row['all_months']).split(',') if m]
                sources.append({
                    'merchant': merchant_name,
                    'count': len(months),
                    'total': float(row['src_income']),
                    'transactions': [],   # per-txn detail is logged for now, not surfaced
                    'showing_limited': False,
                })
            sources.sort(key=lambda s: s['total'], reverse=True)
            source_count = len(sources)

            rg_sal_amount = result.get('rg_sal', {}).get('salary_amount')
            if rg_sal_amount and source_count > 1:
                secondary_income = total_income - rg_sal_amount
                primary_merchant = result['rg_sal']['merchant']
                other_merchants = [
                    s['merchant'] for s in sources
                    if s['merchant'].lower() != primary_merchant.lower()
                ]
                other_str = ', '.join(other_merchants[:2])
                observation = (
                    f"Total estimated monthly income of INR {total_income:,.0f} from "
                    f"{source_count} source{'s' if source_count != 1 else ''}. "
                    f"Primary salary from {primary_merchant} accounts for INR {rg_sal_amount:,.0f}; "
                    f"remaining INR {secondary_income:,.0f} from secondary "
                    f"source{'s' if len(other_merchants) != 1 else ''} ({other_str})."
                )
            else:
                observation = (
                    f"Total estimated monthly income of INR {total_income:,.0f} from "
                    f"{source_count} contributing source{'s' if source_count != 1 else ''}."
                )

            result['rg_income'] = {
                'total_income': total_income,
                'source_count': source_count,
                'sources': sources,
                'observation': observation,
            }
    except Exception as e:
        logger.warning(f"RG income computation failed for [{customer_id}]: {e}")

    return result
