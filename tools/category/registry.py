"""Single source of truth for the L1/L2 category hierarchy.

The L2 (granular) → L1 (broad) mapping mirrors the SQL CASE used upstream.
Everything that needs to ask "is this row a salary?" / "is this row recurring?" /
"what is the L1 bucket for this L2?" should consume this registry instead of
hardcoding string literals.

Renaming a category becomes a one-line edit to L2_TO_L1 / ALIASES below.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional


# ---------------------------------------------------------------------------
# Canonical hierarchy (mirrors the upstream SQL CASE)
# ---------------------------------------------------------------------------

L2_TO_L1: dict[str, str] = {
    # Utility_Bills
    "Mobile_Bills": "Utility_Bills",
    "Gas_bill": "Utility_Bills",
    "Electricity_bill": "Utility_Bills",
    # Mobility
    "Courier_Logistics": "Mobility",
    "Air_Travel": "Mobility",
    "Local_Travel": "Mobility",
    "Taxi_Cab": "Mobility",
    # Finance
    "EMI": "Finance",
    "Loan_Disburse": "Finance",
    "Bank_Fees_Charges": "Finance",
    # Salary
    "Payroll": "Salary",
    "Salary": "Salary",
    # Investment_Insurance
    "Investment": "Investment_Insurance",
    "Insurance": "Investment_Insurance",
    # Govt
    "Recruitment": "Govt",
    "Govt_Tax_Challan": "Govt",
    "Govt_Companies": "Govt",
    # Education
    "Kids_School": "Education",
    "Education": "Education",
    "Books_Stationery": "Education",
    # Food_Restaurants
    "Food": "Food_Restaurants",
    # Shopping_Lifestyle
    "E_Commerce": "Shopping_Lifestyle",
    "Fashion_Beauty": "Shopping_Lifestyle",
    "Electronics_Appliance": "Shopping_Lifestyle",
    "Personal_Home_Services": "Shopping_Lifestyle",
    "Event": "Shopping_Lifestyle",
    "Liquor_Smoke": "Shopping_Lifestyle",
    "Sports_Fitness": "Shopping_Lifestyle",
    "Pet_Care": "Shopping_Lifestyle",
    # Grocery
    "Grocery": "Grocery",
    # E_Lifestyle
    "Digital_Betting_Gaming": "E_Lifestyle",
    "OTT": "E_Lifestyle",
    "Subscription": "E_Lifestyle",
    "Cinema": "E_Lifestyle",
    # Luxury
    "Jewelry_Premium_Gifts": "Luxury",
    "Foreign_Transaction": "Luxury",
    "Charity_Donations": "Luxury",
    # Healthcare
    "Hospital": "Healthcare",
    "Pharmacy": "Healthcare",
    # Rental_Stay
    "Hotel": "Rental_Stay",
    "Rent": "Rental_Stay",
    # Auto_Services
    "Fuel": "Auto_Services",
    "Automobile": "Auto_Services",
    # Credit_Card
    "CC_bill": "Credit_Card",
    # Self_Transfer
    "Self_Transfer": "Self_Transfer",
    # Real_Estate_Housing
    "Real_Estate": "Real_Estate_Housing",
    # B2B
    "Equipment_Construction": "B2B",
    "Engineering": "B2B",
    "MNC_Companies": "B2B",
    # P2P
    "P2P": "P2P",
    "Payment_Services": "P2P",
    "Wallets": "P2P",
    # Transfer (ELSE branch)
    "Cash_Withdrawal": "Transfer",
    "Cash_Deposit": "Transfer",
}

# L1 buckets in display order (mirrors the canonical hierarchy)
L1_ORDER: list[str] = [
    "Salary",
    "Finance",
    "Utility_Bills",
    "Mobility",
    "Auto_Services",
    "Rental_Stay",
    "Real_Estate_Housing",
    "Healthcare",
    "Education",
    "Food_Restaurants",
    "Grocery",
    "Shopping_Lifestyle",
    "E_Lifestyle",
    "Luxury",
    "Investment_Insurance",
    "Credit_Card",
    "Govt",
    "B2B",
    "P2P",
    "Self_Transfer",
    "Transfer",
]


# ---------------------------------------------------------------------------
# Aliases: any raw/legacy string → canonical L2
# ---------------------------------------------------------------------------

ALIASES: dict[str, str] = {
    # raw SQL CASE punctuation / spacing variants
    "Taxi/Cab": "Taxi_Cab",
    "Loan Disburse": "Loan_Disburse",
    "payroll": "Payroll",
    "recruitment": "Recruitment",
    "engineering": "Engineering",
    "cash withdrawl": "Cash_Withdrawal",
    "cash deposit": "Cash_Deposit",
    # legacy values used in older categories.yaml entries
    "E_Entertainment": "OTT",
    "railway recruit": "Recruitment",
    # placeholder L2 values produced by earlier inference step on rgs.csv
    "Salary_Credit": "Salary",
    "UPI_Payment": "P2P",
    "Ride_Hailing": "Taxi_Cab",
}

# Fallback L1 used when an L2 is unknown (mirrors the SQL ELSE branch)
UNKNOWN_L1 = "Transfer"


# ---------------------------------------------------------------------------
# Role tags — replace every hardcoded category literal in the codebase
# ---------------------------------------------------------------------------

ROLES: dict[str, set[str]] = {
    "salary": {"Salary", "Payroll"},
    "emi": {"EMI"},
    "rent": {"Rent"},
    "utility": {"Mobile_Bills", "Gas_bill", "Electricity_bill"},
    "recurring": {
        "Salary", "Payroll", "EMI", "Rent", "Insurance", "Subscription",
        "Mobile_Bills", "Gas_bill", "Electricity_bill", "OTT", "CC_bill",
    },
    "small_ticket": {
        "Food", "Grocery", "Fuel", "Pharmacy", "Mobile_Bills",
        "Bank_Fees_Charges", "Taxi_Cab", "Local_Travel",
    },
    "self_transfer": {"Self_Transfer"},
    "cash": {"Cash_Withdrawal", "Cash_Deposit"},
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def _norm_key(value: str) -> str:
    """Lowercase, strip, and collapse whitespace for alias matching."""
    return " ".join(str(value).strip().split()).lower()


# Build a case-insensitive alias index once
_ALIAS_INDEX: dict[str, str] = {}
for raw, canonical in ALIASES.items():
    _ALIAS_INDEX[_norm_key(raw)] = canonical
# Also accept the canonical name itself (case-insensitive)
for canonical in L2_TO_L1:
    _ALIAS_INDEX.setdefault(_norm_key(canonical), canonical)


def l2_canonical(raw: Optional[str]) -> Optional[str]:
    """Return the canonical L2 for a raw CSV value, or None if blank/unmappable."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in {"nan", "null", "none"}:
        return None
    return _ALIAS_INDEX.get(_norm_key(s))


def l1_of(raw_l2: Optional[str]) -> str:
    """Return the canonical L1 bucket for any L2 value (canonical, alias, or unknown).

    Unknown / blank L2 falls through to the SQL ELSE branch (`Transfer`).
    """
    canonical = l2_canonical(raw_l2)
    if canonical is None:
        return UNKNOWN_L1
    return L2_TO_L1.get(canonical, UNKNOWN_L1)


def l1_values() -> list[str]:
    """All 21 canonical L1 buckets, in display order."""
    return list(L1_ORDER)


def all_l2_values() -> set[str]:
    """All canonical L2 names."""
    return set(L2_TO_L1.keys())


def categories_with_role(role: str) -> set[str]:
    """Canonical L2 names tagged with the given role (`salary`, `recurring`,
    `small_ticket`, `emi`, `rent`, `utility`, `self_transfer`, `cash`).
    """
    return set(ROLES.get(role, set()))


def has_role(raw_l2: Optional[str], role: str) -> bool:
    """True if the given raw L2 (after alias normalisation) is tagged with `role`."""
    canonical = l2_canonical(raw_l2)
    if canonical is None:
        return False
    return canonical in ROLES.get(role, set())


@lru_cache(maxsize=None)
def display_name(value: str) -> str:
    """Pretty-print an L1 or L2 value (underscores → spaces)."""
    if not value:
        return ""
    return str(value).replace("_", " ")
