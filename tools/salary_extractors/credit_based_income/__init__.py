"""Credit-based income: centralised port of the B1..B4 / C / D SQL notebooks.

Public API:
    _calculate_credit_based_income(df=..., cust_id_filter=[...]) -> pd.DataFrame
"""

from .extractor import (
    CreditBasedIncomeExtractor,
    _calculate_credit_based_income,
)

__all__ = ["_calculate_credit_based_income", "CreditBasedIncomeExtractor"]
