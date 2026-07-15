"""Tools module - analytics functions and tool registry."""

from .analytics import (
    debit_total,
    get_spending_by_category,
    top_spending_categories,
    spending_in_date_range,
    get_total_income,
    list_customers,
    list_categories,
)

from . import analytics

from .category.resolver import category_presence_lookup
