"""Intent to tool mapping configuration."""

from schemas.intent import IntentType

INTENT_TOOL_MAP = {
    # Existing intents
    IntentType.TOTAL_SPENDING: ["debit_total"],
    IntentType.TOTAL_INCOME: ["get_total_income"],
    IntentType.SPENDING_BY_CATEGORY: ["get_spending_by_category"],
    IntentType.ALL_CATEGORIES_SPENDING: ["get_spending_by_category"],
    IntentType.TOP_CATEGORIES: ["top_spending_categories"],
    IntentType.SPENDING_IN_PERIOD: ["spending_in_date_range"],
    IntentType.FINANCIAL_OVERVIEW: [
        "get_total_income",
        "debit_total",
        "top_spending_categories"
    ],
    IntentType.COMPARE_CATEGORIES: [
        "get_spending_by_category",
        "get_spending_by_category"
    ],
    IntentType.LIST_CUSTOMERS: ["list_customers"],
    IntentType.LIST_CATEGORIES: ["list_categories"],

    # New report-oriented intents
    IntentType.CUSTOMER_REPORT: ["generate_customer_report"],
    IntentType.LENDER_PROFILE: ["generate_lender_profile"],
    IntentType.CREDIT_ANALYSIS: ["get_credit_statistics"],
    IntentType.DEBIT_ANALYSIS: ["get_debit_statistics", "top_spending_categories", "debit_total"],
    IntentType.TRANSACTION_STATISTICS: ["get_transaction_counts", "get_credit_statistics"],
    IntentType.ANOMALY_DETECTION: ["detect_anomalies"],
    IntentType.BALANCE_TREND: ["get_balance_trend"],
    IntentType.INCOME_STABILITY: ["get_income_stability"],
    IntentType.CASH_FLOW: ["get_cash_flow"],

    # Category presence lookup
    IntentType.CATEGORY_PRESENCE_LOOKUP: ["category_presence_lookup"],

    IntentType.UNKNOWN: [],
}

REQUIRED_FIELDS = {
    # Existing intents
    IntentType.TOTAL_SPENDING: ["customer_id"],
    IntentType.TOTAL_INCOME: ["customer_id"],
    # IntentType.SPENDING_BY_CATEGORY: ["customer_id", "category"],
    IntentType.SPENDING_BY_CATEGORY: ["customer_id"],

    IntentType.ALL_CATEGORIES_SPENDING: ["customer_id"],
    IntentType.TOP_CATEGORIES: ["customer_id"],
    IntentType.SPENDING_IN_PERIOD: ["customer_id", "start_date", "end_date"],
    IntentType.FINANCIAL_OVERVIEW: ["customer_id"],
    IntentType.COMPARE_CATEGORIES: ["customer_id", "categories"],
    IntentType.LIST_CUSTOMERS: [],
    IntentType.LIST_CATEGORIES: [],

    # New report-oriented intents
    IntentType.CUSTOMER_REPORT: ["customer_id"],
    IntentType.LENDER_PROFILE: ["customer_id"],
    IntentType.CREDIT_ANALYSIS: ["customer_id"],
    IntentType.DEBIT_ANALYSIS: ["customer_id"],
    IntentType.TRANSACTION_STATISTICS: ["customer_id"],
    IntentType.ANOMALY_DETECTION: ["customer_id"],
    IntentType.BALANCE_TREND: ["customer_id"],
    IntentType.INCOME_STABILITY: ["customer_id"],
    IntentType.CASH_FLOW: ["customer_id"],

    # Category presence lookup
    IntentType.CATEGORY_PRESENCE_LOOKUP: ["customer_id", "category"],

    IntentType.UNKNOWN: [],
}

MAX_TOOLS_PER_QUERY = 5
