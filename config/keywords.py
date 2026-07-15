"""Centralised keyword definitions for transaction classification.

All narration-matching keywords live here so that changes to detection
logic only require editing one file.  Consumer modules import from here
instead of defining their own local copies.
"""

# ---------------------------------------------------------------------------
# Salary detection
# ---------------------------------------------------------------------------
# General salary narration keywords (case-insensitive matching)
SALARY_KEYWORDS = [
    "salary", "employee", "payroll", "stipend", "bonus", "wages",
]

# Shorter / uppercase fragments used to filter routine salary credits
# in anomaly detectors (matched against uppercased narrations)
SALARY_CREDIT_FRAGMENTS = ("SALARY", " SAL ", "SAL/", "PAYROLL")

# ---------------------------------------------------------------------------
# Self-transfer detection
# ---------------------------------------------------------------------------
SELF_TRANSFER_KEYWORDS = [
    "SELF", "OWN A/C", "OWN ACCOUNT", "OWNACCOUNT",
    "SELF TRF", "SELF TRANSFER", "SELF-TRANSFER",
]

# ---------------------------------------------------------------------------
# Loan / lender detection
# ---------------------------------------------------------------------------
# Known NBFC / bank lender name fragments for loan disbursal detection
LENDER_FRAGMENTS = [
    "HDFC BANK", "ICICI BANK", "AXIS BANK", "KOTAK BANK", "KOTAK MAHINDRA",
    "SBI ", "STATE BANK", "BAJAJ FINANCE", "BAJAJ FINSERV", "TATA CAPITAL",
    "FULLERTON", "ADITYA BIRLA", "PIRAMAL", "MUTHOOT", "MANAPPURAM",
    "L&T FINANCE", "PAYSENSE", "CASHE", "MONEYVIEW", "STASHFIN", "NAVI ",
    "PREFR", "KREDITBEE", "LENDINGKART", "INDIFI", "CLIX CAPITAL",
    "YES BANK", "IDFC BANK", "INDUSIND BANK",
]

LOAN_DISBURSEMENT_KEYWORDS = [
    "LOAN DIS", "LOAN DISB", "LOAN DISBURS",
    "LOAN CREDIT", "LOAN A/C CR", "SANCTIONED",
    "LOAN AC NO", "PLCC", " PDL ",
]

# ---------------------------------------------------------------------------
# Mandate / NACH EMI detection
# ---------------------------------------------------------------------------
MANDATE_EMI_KEYWORDS = ["NACH-10", "SPLN"]

# ---------------------------------------------------------------------------
# EMI narration keywords (core-banking formats)
# ---------------------------------------------------------------------------
EMI_NARRATION_KEYWORDS = ["/EMI ", "/EMIP", " EMI "]

# ---------------------------------------------------------------------------
# Home loan EMI detection
# ---------------------------------------------------------------------------
HOME_LOAN_EMI_KEYWORDS = ["HOUSE LOAN", "HOME LOAN", "HOMELOAN"]

# ---------------------------------------------------------------------------
# Merged EMI keyword set
# ---------------------------------------------------------------------------
# Single source of truth for "is this transaction an EMI payment?".
# Combines mandate/NACH, core-banking EMI narrations, and home-loan EMIs.
# Used by pipeline/reports/customer_report_builder._get_emi_block to drive
# the Loan Activity (Loan EMIs) cards in bank_report_v2.html.
EMI_ALL_KEYWORDS = (
    MANDATE_EMI_KEYWORDS
    + EMI_NARRATION_KEYWORDS
    + HOME_LOAN_EMI_KEYWORDS
)

# ---------------------------------------------------------------------------
# Credit card payment detection
# ---------------------------------------------------------------------------
CC_PAYMENT_KEYWORDS = [
    "CREDIT CARD PAYMENT", "BILL PAID TO CREDIT CARD",
    "CREDIT CARD BILL", "CC PAY", "CARD DUES",
    "CC DUES", "CREDITCARD", "CC BILL"
]

# ---------------------------------------------------------------------------
# Land payment detection
# ---------------------------------------------------------------------------
LAND_PAYMENT_KEYWORDS = [":LAND PAYMENT", " LAND PAYMENT "]

# ---------------------------------------------------------------------------
# ATM withdrawal detection
# ---------------------------------------------------------------------------
# Narration format: ATL/<terminal>/<id>/<address>/<time>
ATM_WITHDRAWAL_KEYWORDS = ["%ATL/%", "ATI/%", "ATW/%"]

# ---------------------------------------------------------------------------
# ECS / NACH bounce detection
# ---------------------------------------------------------------------------
ECS_BOUNCE_KEYWORDS = [
    "ECS%RETURN", "ECS%BOUNCE", "NACH%RETURN", "NACH%BOUNCE",
    "ECS%DISHON", "NACH%DISHON", "MANDATE%REJECT",
    "ECS%UNPAID", "NACH%UNPAID", "INSUFFICIENT FUND","I/W CHQ RTN"
]

# ---------------------------------------------------------------------------
# Account-quality helpers
# ---------------------------------------------------------------------------
# Categories that indicate the account is used for everyday spending
SMALL_TICKET_CATEGORIES = {
    "Food_Restaurants", "Food", "Grocery", "Grocery_Supermarket",
    "Fuel", "Pharmacy_Medical", "Pharmacy", "Shopping",
    "Transport", "Entertainment", "Supermarket",
}

# ---------------------------------------------------------------------------
# Event-detection keyword rules
# ---------------------------------------------------------------------------
# Fields:
#   type        — event type key
#   direction   — "C" (credit), "D" (debit), "any"
#   keywords    — list of narration substrings (any match, case-insensitive)
#   significance — "high" / "medium" / "positive"
#   label       — human-readable short name
#   min_months  — (optional) skip if appears in fewer distinct calendar months

EVENT_KEYWORD_RULES = [
    # ── Stress signals ────────────────────────────────────────────────────
    {
        "type": "pf_withdrawal",
        "direction": "C",
        "keywords": [
            "EPFO", "PF SETTL", "PF FINAL", "PF WITHDRAWAL",
            "PROVIDENT FUND", "PPF CLOSURE", "PF CREDIT",
        ],
        "significance": "high",
        "label": "PF/Provident Fund withdrawal",
    },
    {
        "type": "fd_closure",
        "direction": "C",
        "keywords": [
            "FD CLOSURE", "FIXED DEPOSIT CLO", "FD MATURITY",
            "PREMATURE CLOSURE", "FD PREMATURE",
        ],
        "significance": "medium",
        "label": "FD premature/maturity closure",
    },
    {
        "type": "salary_advance_bnpl",
        "direction": "C",
        "keywords": [
            "EARLY SALARY", "LAZYPAY", "SIMPL", "SLICE ",
            "KREDITBEE", "MONEYVIEW", "FIBE ", "NIRO",
            "STASHFIN", "MPOKKET", "FREO", "SALARY ADVANCE",
        ],
        "significance": "high",
        "label": "Salary advance / BNPL credit",
    },
    # ── Positive signals ──────────────────────────────────────────────────
    {
        "type": "sip_investment",
        "direction": "D",
        "keywords": [
            "SIP", "MUTUAL FUND", " MF ", "MF/",
            "BSE STAR MF", "NSE MFUND", "CAMS ", "KARVY ",
        ],
        "significance": "positive",
        "min_months": 2,
        "label": "SIP / Mutual Fund investment",
    },
    {
        "type": "insurance_premium",
        "direction": "D",
        "keywords": [
            "LIC ", "HDFC LIFE", "ICICI PRU", "MAX LIFE",
            "SBI LIFE", "TERM INSURANCE", "INSURANCE PREM",
            "LIFE INS", "BAJAJ ALLIANZ", "KOTAK LIFE",
        ],
        "significance": "positive",
        "min_months": 2,
        "label": "Life / term insurance premium",
    },
    # ── Other notable income events ───────────────────────────────────────
    {
        "type": "govt_benefit",
        "direction": "C",
        "keywords": [
            "PM KISAN", "MNREGA", "DBT ", "GOVT BENEFIT",
            "SCHOLARSHIP", "PENSION CREDIT", "JANDHAN",
        ],
        "significance": "medium",
        "label": "Government benefit / pension credit",
    },
    # ── ECS / NACH bounce / return ────────────────────────────────────────
    {
        "type": "ecs_bounce",
        "direction": "D",
        "keywords": ECS_BOUNCE_KEYWORDS,
        "significance": "high",
        "label": "ECS/NACH bounce / return",
    },
    # ── Mandate / NACH EMI debits ────────────────────────────────────────
    {
        "type": "mandate_emi",
        "direction": "D",
        "keywords": MANDATE_EMI_KEYWORDS,
        "significance": "medium",
        "label": "NACH mandate / SPLN EMI debit",
        "min_months": 2,
    },
    # ── EMI narration patterns (core-banking formats) ────────────────────
    {
        "type": "emi_debit",
        "direction": "D",
        "keywords": EMI_NARRATION_KEYWORDS,
        "significance": "medium",
        "label": "EMI payment debit",
        "min_months": 2,
    },
    # ── Home loan EMI ────────────────────────────────────────────────────
    {
        "type": "home_loan_emi",
        "direction": "D",
        "keywords": HOME_LOAN_EMI_KEYWORDS,
        "significance": "medium",
        "label": "Home loan EMI payment",
        "min_months": 2,
    },
    # ── Credit card bill payments ────────────────────────────────────────
    {
        "type": "cc_payment",
        "direction": "D",
        "keywords": CC_PAYMENT_KEYWORDS,
        "significance": "positive",
        "label": "Credit card bill payment",
        "min_months": 2,
    },
    # ── Land payment ─────────────────────────────────────────────────────
    {
        "type": "land_payment",
        "direction": "D",
        "keywords": LAND_PAYMENT_KEYWORDS,
        "significance": "medium",
        "label": "Land purchase payment",
    },
    # ── Loan disbursal credit ────────────────────────────────────────────
    {
        "type": "loan_disbursal",
        "direction": "C",
        "keywords": LOAN_DISBURSEMENT_KEYWORDS,
        "significance": "high",
        "label": "Loan disbursal credit received",
    },
]
