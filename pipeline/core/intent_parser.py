"""Intent parser using LLM to extract structured intent from user query."""

import json
import logging
import re
from difflib import get_close_matches
from utils.llm_factory import create_chat_model

from schemas.intent import ParsedIntent, IntentType, CONFIDENCE_THRESHOLD_RETRY
from config.settings import PARSER_MODEL, LLM_SEED, LLM_TEMPERATURE
from config.prompts import PARSER_PROMPT

logger = logging.getLogger(__name__)


# All valid categories for normalization
VALID_CATEGORIES = [
    "MNC_Companies", "Digital_Betting_Gaming", "Food", "Liquor_Smoke",
    "Bank_Fees_Charges", "Mobile_Bills", "Wallets", "E_Commerce",
    "Courier_Logistics", "Air_Travel", "E_Entertainment", "Mobility",
    "Railway", "Govt_Tax_Challan", "Hospital", "Grocery",
    "Fashion_Beauty", "Equipment_Construction", "Pharmacy", "Engineering",
    "Kids_School", "Education", "Rent", "Jewelry_Premium_Gifts",
    "Foreign_Transaction", "Payroll", "Investment", "Salary",
    "Electronics_Appliance", "Charity_Donations", "Books_Stationery",
    "Fuel", "Govt_Companies", "Hotel", "Insurance",
    "Personal_Home_Services", "Pet_Care", "Taxi_Cab", "Real_Estate",
    "Sports_Fitness", "EMI", "Finance", "P2P"
]

# All valid intents (excluding UNKNOWN)
VALID_INTENTS = [
    "total_spending", "total_income", "spending_by_category", "all_categories_spending", "top_categories",
    "spending_in_period", "financial_overview", "compare_categories",
    "list_customers", "list_categories",
    "customer_report", "lender_profile", "credit_analysis", "debit_analysis",
    "transaction_statistics", "anomaly_detection", "balance_trend",
    "income_stability", "cash_flow",
    "category_presence_lookup",
]



def normalize_category_name(category: str) -> str | None:
    """Normalize category name using case-insensitive matching."""
    if not category:
        return None

    category_lower = category.lower().strip()
    category_map = {cat.lower(): cat for cat in VALID_CATEGORIES}

    if category_lower in category_map:
        return category_map[category_lower]

    # Fuzzy matching for typos
    matches = get_close_matches(category_lower, list(category_map.keys()), n=1, cutoff=0.7)
    if matches:
        return category_map[matches[0]]

    return None


def validate_intent_name(intent_str: str) -> IntentType:
    """Validate and normalize intent string to IntentType enum."""
    if not intent_str:
        return IntentType.UNKNOWN

    intent_lower = intent_str.lower().strip()

    try:
        return IntentType(intent_lower)
    except ValueError:
        # Fuzzy match for typos
        matches = get_close_matches(intent_lower, VALID_INTENTS, n=1, cutoff=0.6)
        if matches:
            try:
                return IntentType(matches[0])
            except ValueError:
                pass
        return IntentType.UNKNOWN


def calculate_confidence(parsed: dict, query: str) -> float:
    """Calculate dynamic confidence score based on extraction quality."""
    score = 0.5  # Base score

    # Intent quality
    if parsed.get("intent") and parsed["intent"] != "unknown":
        score += 0.2

    # Customer ID presence (if query mentions customer)
    query_lower = query.lower()
    if parsed.get("customer_id") is not None:
        score += 0.15
    elif "customer" in query_lower and parsed.get("customer_id") is None:
        score -= 0.1  # Penalty: query mentions customer but not extracted

    # Category extraction quality
    if parsed.get("category") or parsed.get("categories"):
        normalized = normalize_category_name(parsed.get("category", ""))
        if normalized:
            score += 0.1
        elif parsed.get("categories"):
            score += 0.1

    # Date extraction quality
    if parsed.get("start_date") and parsed.get("end_date"):
        # Check format validity
        date_pattern = r"^\d{4}-\d{2}-\d{2}$"
        if re.match(date_pattern, parsed["start_date"]) and re.match(date_pattern, parsed["end_date"]):
            score += 0.1

    return min(max(score, 0.0), 1.0)


class IntentParser:
    def __init__(self, model_name: str = PARSER_MODEL):
        self.llm = create_chat_model(model_name, json_mode=True)

    def parse(self, query: str) -> ParsedIntent:
        prompt = PARSER_PROMPT.format(query=query)

        try:
            response = self.llm.invoke(prompt)
            content = response.content.strip()

            # With format="json", output should be clean JSON
            data = json.loads(content)

            # Validate and normalize intent
            intent_str = data.get("intent", "unknown")
            data["intent"] = validate_intent_name(intent_str)

            # Skip normalization for category-presence (resolver handles fuzzy aliases)
            _skip_category_norm = {IntentType.CATEGORY_PRESENCE_LOOKUP}
            if data.get("category"):
                if data["intent"] not in _skip_category_norm:
                    normalized = normalize_category_name(data["category"])
                    data["category"] = normalized

            # Normalize categories list if present
            if data.get("categories") and isinstance(data["categories"], list):
                normalized_cats = []
                for cat in data["categories"]:
                    normalized = normalize_category_name(cat)
                    if normalized:
                        normalized_cats.append(normalized)
                data["categories"] = normalized_cats if normalized_cats else None

            # Calculate confidence dynamically
            data["confidence"] = calculate_confidence(data, query)
            data["raw_query"] = query

            # Post-processing corrections for common misclassifications
            query_lower = query.lower()
            if (data["intent"] == IntentType.SPENDING_BY_CATEGORY and
                not data.get("category") and
                any(kw in query_lower for kw in ["total spending", "total expense", "spend in total"])):
                # Correct misclassification: "total spending" should be TOTAL_SPENDING, not SPENDING_BY_CATEGORY
                data["intent"] = IntentType.TOTAL_SPENDING
                data["confidence"] = min(data["confidence"] + 0.1, 1.0)  # Boost confidence for correction

            # Clean up null string values
            for key in ["category", "start_date", "end_date"]:
                if data.get(key) in ["null", "None", ""]:
                    data[key] = None

            return ParsedIntent(**data)

        except json.JSONDecodeError as e:
            logger.warning("JSON parse error from LLM: %s | Raw: %s", e, response.content[:300])
            return self._fallback_parse(query)
        except Exception as e:
            logger.error("Intent parse error: %s", e)
            return ParsedIntent(intent=IntentType.UNKNOWN, raw_query=query, confidence=0.0)

    def _fallback_parse(self, query: str) -> ParsedIntent:
        """Enhanced regex fallback when LLM JSON fails."""
        query_lower = query.lower()

        # Extract customer ID (multiple patterns)
        customer_id = None
        cust_patterns = [
            r'customer\s*[#:]?\s*(\d+)',
            r'cust(?:omer)?[_\s]?id\s*[=:]?\s*(\d+)',
            r'for\s+customer\s+(\d+)',
            r'for\s+(\d{10})',  # 10-digit phone number
            r'for\s+(\d+)',
            r'^(\d+)\s',  # ID at start
            r'(\d{10})',  # 10-digit phone number anywhere
        ]
        for pattern in cust_patterns:
            match = re.search(pattern, query_lower)
            if match:
                customer_id = int(match.group(1))
                break

        # Detect intent with priority ordering (most specific first)
        intent = IntentType.UNKNOWN

        # Category presence lookup patterns (check first - high priority)
        presence_patterns = [
            (r'does\s+(?:he|she|customer|they)\s+(?:spend|pay|have)\s+(?:on|for)?\s*(.+?)(?:\?|$)', True),
            (r'(?:is|are)\s+there\s+(?:any)?\s*(.+?)\s+(?:transactions?|expenses?|spending|activity)', True),
            (r'does\s+(?:he|she|customer|they)\s+receive\s+(.+?)(?:\?|$)', True),
            (r'any\s+(.+?)\s+(?:activity|transactions?|spending|expenses?)', True),
            (r'check\s+(?:for)?\s*(.+?)\s+(?:transactions?|presence)', True),
        ]

        for pattern, _ in presence_patterns:
            match = re.search(pattern, query_lower)
            if match:
                extracted_category = match.group(1).strip()
                # Clean up extracted category
                extracted_category = re.sub(r'\s+(transactions?|expenses?|spending|activity).*$', '', extracted_category)
                # Try to resolve to known category via alias
                from config.category_loader import resolve_category_alias
                resolved = resolve_category_alias(extracted_category)
                return ParsedIntent(
                    intent=IntentType.CATEGORY_PRESENCE_LOOKUP,
                    customer_id=customer_id,
                    category=resolved or extracted_category,
                    raw_query=query,
                    confidence=0.75
                )

        if any(kw in query_lower for kw in ["full report", "customer report", "comprehensive report", "complete report", "generate report", "create report", "make report", "report for", "generate a report", "pdf report"]):
            intent = IntentType.CUSTOMER_REPORT
        elif any(kw in query_lower for kw in ["lender", "creditworth", "lending", "loan", "credit profile", "underwriting"]):
            intent = IntentType.LENDER_PROFILE
        elif any(kw in query_lower for kw in ["anomal", "spike", "unusual", "outlier", "irregular"]):
            intent = IntentType.ANOMALY_DETECTION
        elif any(kw in query_lower for kw in ["balance trend", "running balance", "balance over time"]):
            intent = IntentType.BALANCE_TREND
        elif any(kw in query_lower for kw in ["income stability", "salary regularity", "income consistent"]):
            intent = IntentType.INCOME_STABILITY
        elif any(kw in query_lower for kw in ["cash flow", "inflow", "outflow"]):
            intent = IntentType.CASH_FLOW
        elif any(kw in query_lower for kw in ["credit analysis", "credit stats", "income analysis", "max credit"]):
            intent = IntentType.CREDIT_ANALYSIS
        elif any(kw in query_lower for kw in ["debit analysis", "spending analysis", "expense analysis"]):
            intent = IntentType.DEBIT_ANALYSIS
        elif any(kw in query_lower for kw in ["transaction count", "how many transaction", "transaction stats"]):
            intent = IntentType.TRANSACTION_STATISTICS

        # Existing intents
        elif any(kw in query_lower for kw in ["all categories", "spending by category", "category breakdown", "spend by category"]):
            intent = IntentType.ALL_CATEGORIES_SPENDING
        elif "compare" in query_lower and "categor" in query_lower:
            intent = IntentType.COMPARE_CATEGORIES
        elif "top" in query_lower and "categor" in query_lower:
            intent = IntentType.TOP_CATEGORIES
        elif any(kw in query_lower for kw in ["total spending", "spend in total", "total expense"]):
            intent = IntentType.TOTAL_SPENDING
        elif any(kw in query_lower for kw in ["total income", "total credit", "how much earned"]):
            intent = IntentType.TOTAL_INCOME
        elif "overview" in query_lower or "summary" in query_lower:
            intent = IntentType.FINANCIAL_OVERVIEW
        elif "list customer" in query_lower or "all customer" in query_lower:
            intent = IntentType.LIST_CUSTOMERS
        elif "list categor" in query_lower or "all categor" in query_lower:
            intent = IntentType.LIST_CATEGORIES

        # Extract category (single)
        category = None
        for cat in VALID_CATEGORIES:
            if cat.lower() in query_lower:
                category = cat
                break

        # Check for category-specific spending
        if category and intent == IntentType.UNKNOWN:
            intent = IntentType.SPENDING_BY_CATEGORY

        # Extract multiple categories for comparison
        categories = None
        if intent == IntentType.COMPARE_CATEGORIES or ("vs" in query_lower or "versus" in query_lower or "compare" in query_lower):
            found_cats = []
            for cat in VALID_CATEGORIES:
                if cat.lower() in query_lower:
                    found_cats.append(cat)
            if len(found_cats) >= 2:
                categories = found_cats
                intent = IntentType.COMPARE_CATEGORIES

        # Extract dates
        start_date = None
        end_date = None
        date_pattern = r'(\d{4}-\d{2}-\d{2})'
        dates = re.findall(date_pattern, query)
        if len(dates) >= 2:
            start_date = dates[0]
            end_date = dates[1]
            if intent == IntentType.UNKNOWN:
                intent = IntentType.SPENDING_IN_PERIOD

        # Calculate confidence for fallback
        confidence = 0.5
        if customer_id:
            confidence += 0.15
        if intent != IntentType.UNKNOWN:
            confidence += 0.15
        if category or categories:
            confidence += 0.1

        return ParsedIntent(
            intent=intent,
            customer_id=customer_id,
            category=category,
            categories=categories,
            start_date=start_date,
            end_date=end_date,
            raw_query=query,
            confidence=confidence
        )

