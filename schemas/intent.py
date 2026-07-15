"""Intent schema for parsed user queries."""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class IntentType(str, Enum):
    # Existing intents
    TOTAL_SPENDING = "total_spending"
    TOTAL_INCOME = "total_income"
    SPENDING_BY_CATEGORY = "spending_by_category"
    ALL_CATEGORIES_SPENDING = "all_categories_spending"
    TOP_CATEGORIES = "top_categories"
    SPENDING_IN_PERIOD = "spending_in_period"
    FINANCIAL_OVERVIEW = "financial_overview"
    COMPARE_CATEGORIES = "compare_categories"
    LIST_CUSTOMERS = "list_customers"
    LIST_CATEGORIES = "list_categories"

    # New report-oriented intents
    CUSTOMER_REPORT = "customer_report"
    LENDER_PROFILE = "lender_profile"
    CREDIT_ANALYSIS = "credit_analysis"
    DEBIT_ANALYSIS = "debit_analysis"
    TRANSACTION_STATISTICS = "transaction_statistics"
    ANOMALY_DETECTION = "anomaly_detection"
    BALANCE_TREND = "balance_trend"
    INCOME_STABILITY = "income_stability"
    CASH_FLOW = "cash_flow"

    # Category presence lookup
    CATEGORY_PRESENCE_LOOKUP = "category_presence_lookup"

    UNKNOWN = "unknown"


class ParsedIntent(BaseModel):
    """Structured output from intent parser."""
    intent: IntentType
    customer_id: Optional[int] = None
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    top_n: Optional[int] = Field(default=5)
    threshold_std: Optional[float] = Field(default=2.0, description="Standard deviation threshold for anomaly detection")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    raw_query: str = ""


# Confidence threshold for retry logic
CONFIDENCE_THRESHOLD_RETRY = 0.6
CONFIDENCE_THRESHOLD_LOW = 0.4
