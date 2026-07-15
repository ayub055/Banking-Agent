"""Query planner - validates intent and creates execution plan."""

from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from schemas.intent import ParsedIntent, IntentType
from config.intents import INTENT_TOOL_MAP, REQUIRED_FIELDS, MAX_TOOLS_PER_QUERY
from data.loader import get_transactions_df


def validate_date_format(date_str: str) -> Tuple[bool, str]:
    """Validate date string is in YYYY-MM-DD format and is a real date."""
    if not date_str:
        return False, "Date is empty"

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True, ""
    except ValueError:
        return False, f"Invalid date format '{date_str}'. Expected YYYY-MM-DD"


def validate_date_range(start_date: str, end_date: str) -> Tuple[bool, str]:
    """Validate date range: format and logical order."""
    valid, error = validate_date_format(start_date)
    if not valid:
        return False, f"Start date error: {error}"

    valid, error = validate_date_format(end_date)
    if not valid:
        return False, f"End date error: {error}"

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    if start > end:
        return False, f"Start date ({start_date}) must be before or equal to end date ({end_date})"

    return True, ""


def normalize_category(category: str, valid_categories: set) -> Optional[str]:
    """Case-insensitive category matching. Returns normalized category or None."""
    if not category:
        return None

    category_lower = category.lower().strip()

    # Build lowercase lookup map
    category_map = {cat.lower(): cat for cat in valid_categories}

    if category_lower in category_map:
        return category_map[category_lower]

    # Try partial matching for common typos
    for valid_lower, valid_original in category_map.items():
        if category_lower in valid_lower or valid_lower in category_lower:
            return valid_original

    return None


class QueryPlanner:
    def __init__(self):
        self._load_valid_values()

    def _load_valid_values(self):
        from tools.category.registry import l1_values, all_l2_values
        df = get_transactions_df()
        self.valid_customers = set(df['cust_id'].unique())
        # Accept both grains for NL queries — registry is authoritative.
        self.valid_categories = set(l1_values()) | all_l2_values()
        self.category_map = {cat.lower(): cat for cat in self.valid_categories}

    def create_plan(self, intent: ParsedIntent) -> Tuple[List[Dict[str, Any]], str]:
        """Returns (execution_plan, error_message). Error is empty if valid."""

        error = self._validate_intent(intent)
        if error:
            return [], error

        tools = INTENT_TOOL_MAP.get(intent.intent, [])
        if len(tools) > MAX_TOOLS_PER_QUERY:
            return [], f"Too many tools required: {len(tools)}"

        plan = self._build_plan(intent, tools)
        return plan, ""

    def _validate_intent(self, intent: ParsedIntent) -> str:
        if intent.intent == IntentType.UNKNOWN:
            return "Could not understand the query"

        required = REQUIRED_FIELDS.get(intent.intent, [])

        if "customer_id" in required:
            if intent.customer_id is None:
                return "Customer ID is required"
            if intent.customer_id not in self.valid_customers:
                return f"Customer {intent.customer_id} not found. Valid customers: {sorted(self.valid_customers)[:10]}"

        # Validate and normalize single category
        if "category" in required:
            if not intent.category:
                return "Category is required"
            # For CATEGORY_PRESENCE_LOOKUP, skip strict validation
            # The category resolver handles fuzzy matching via YAML config
            if intent.intent != IntentType.CATEGORY_PRESENCE_LOOKUP:
                normalized = normalize_category(intent.category, self.valid_categories)
                if normalized is None:
                    return f"Invalid category: '{intent.category}'. Valid categories: {sorted(self.valid_categories)}"
                # Update intent with normalized category (mutable field)
                intent.category = normalized

        # Validate and normalize multiple categories
        if "categories" in required:
            if not intent.categories or len(intent.categories) < 2:
                return "At least 2 categories required for comparison"

            normalized_categories = []
            for cat in intent.categories:
                normalized = normalize_category(cat, self.valid_categories)
                if normalized is None:
                    return f"Invalid category: '{cat}'. Valid categories: {sorted(self.valid_categories)}"
                normalized_categories.append(normalized)
            # Update intent with normalized categories
            intent.categories = normalized_categories

        # Validate date fields with format and logic checks
        if "start_date" in required or "end_date" in required:
            if not intent.start_date:
                return "Start date is required"
            if not intent.end_date:
                return "End date is required"

            valid, error = validate_date_range(intent.start_date, intent.end_date)
            if not valid:
                return error

        return ""

    def _build_plan(self, intent: ParsedIntent, tools: List[str]) -> List[Dict[str, Any]]:
        plan = []

        for tool_name in tools:
            args = self._get_tool_args(intent, tool_name)
            plan.append({"tool": tool_name, "args": args})

        if intent.intent == IntentType.COMPARE_CATEGORIES and intent.categories:
            plan = []
            for cat in intent.categories:
                plan.append({
                    "tool": "get_spending_by_category",
                    "args": {"customer_id": intent.customer_id, "category": cat}
                })

        return plan

    def _get_tool_args(self, intent: ParsedIntent, tool_name: str) -> Dict[str, Any]:
        args = {}

        if tool_name in [
            "debit_total",
            "get_total_income",
            "get_credit_statistics",
            "get_debit_statistics",
            "get_transaction_counts",
            "get_balance_trend",
            "get_income_stability",
            "get_cash_flow",
            "generate_customer_report",
            "generate_lender_profile",
        ]:
            args["customer_id"] = intent.customer_id

        elif tool_name == "get_spending_by_category":
            args["customer_id"] = intent.customer_id
            if intent.category:
                args["category"] = intent.category

        elif tool_name == "top_spending_categories":
            args["customer_id"] = intent.customer_id
            args["top_n"] = intent.top_n or 5

        elif tool_name == "spending_in_date_range":
            args["customer_id"] = intent.customer_id
            args["start_date"] = intent.start_date
            args["end_date"] = intent.end_date

        elif tool_name == "detect_anomalies":
            args["customer_id"] = intent.customer_id
            args["threshold_std"] = intent.threshold_std or 2.0

        elif tool_name == "category_presence_lookup":
            args["customer_id"] = intent.customer_id
            args["category"] = intent.category

        return args
