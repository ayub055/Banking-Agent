"""Business-rule thresholds for the banking report.

All numeric cut-offs live here so a risk analyst can tune the decisioning
logic in a single place without touching pipeline code. Consumers:
event detection (tools/event_detector.py), merchant features
(tools/merchant_features.py), the banking checklist
(pipeline/reports/checklist_builder.py), and account-quality classification
(tools/account_quality.py).
"""

# ---------------------------------------------------------------------------
# Credit-to-Spend Timing  (event_detector — credit_spend_dependency)
# ---------------------------------------------------------------------------
CREDIT_SPEND_MIN_AMOUNT: int = 10000        # Minimum credit amount to analyze
CREDIT_SPEND_MIN_RATIO: float = 0.20        # Min credit as fraction of median monthly credit
CREDIT_SPEND_WINDOW_DAYS: int = 3           # Calendar days to look forward for debits
CREDIT_SPEND_HIGH_THRESHOLD: float = 0.80   # ≥ 80 % spent within window → high significance
CREDIT_SPEND_MEDIUM_THRESHOLD: float = 0.60 # ≥ 60 % spent within window → medium significance

# ---------------------------------------------------------------------------
# Post-Disbursement Usage  (event_detector — post_disbursement_usage)
# ---------------------------------------------------------------------------
POST_DISB_WINDOW_DAYS: int = 7             # Days after disbursement to analyze debits
POST_DISB_MIN_AMOUNT: int = 50000          # Min disbursement amount to trigger analysis
POST_DISB_MATCH_TOLERANCE: float = 0.15    # Debits within ±15 % of disbursement → "≈ equal"
POST_DISB_CONCENTRATION_PCT: float = 0.50  # ≥ 50 % of disbursement going to top recipients → flag
POST_DISB_MIN_DEBIT: int = 5000            # Ignore debits below this amount

# ---------------------------------------------------------------------------
# Merchant Features — Banking Report
# ---------------------------------------------------------------------------
MERCHANT_FAVOURITE_TOP_N: int = 2          # Number of favourite merchants to highlight
MERCHANT_SIGNIFICANT_PCT: float = 0.25     # ≥ 25 % of total flow = significant counterparty

# ---------------------------------------------------------------------------
# Mode-wise Distribution Shift  (checklist — banking)
# ---------------------------------------------------------------------------
MODE_SHIFT_RECENT_MONTHS: int = 2           # Recent window = last 2 calendar months
MODE_SHIFT_THRESHOLD_PP: float = 15.0       # Flag if any mode shifts ≥ 15 percentage points
MODE_SHIFT_MIN_TRANSACTIONS: int = 5        # Min txns per period to compare
MODE_SHIFT_MIN_MONTHS: int = 3              # Need ≥ 3 distinct months of data

# ---------------------------------------------------------------------------
# Account Quality (CONDUIT / PRIMARY / SECONDARY classification)
# ---------------------------------------------------------------------------
AQ_PRIMARY_SCORE: int = 60                  # score >= 60 -> primary account
AQ_SECONDARY_SCORE: int = 40                # score >= 40 -> secondary (else conduit)
AQ_CONFIDENCE_HIGH_SCORE: int = 75          # score >= 75 -> high confidence
AQ_SALARY_CONDUIT_OUTFLOW_PCT: float = 40   # outflow >= 40% of salary in window -> conduit signal
AQ_ATM_HIGH_PCT: float = 50                 # ATM debit % > 50 -> high cash dependency
AQ_ATM_MODERATE_PCT: float = 30             # ATM debit % > 30 -> moderate cash dependency
AQ_LOW_ACTIVITY_DEBITS: int = 10            # avg monthly debits < 10 -> low activity
AQ_HIGH_ACTIVITY_DEBITS: int = 20           # avg monthly debits > 20 -> high activity
AQ_CONDUIT_MAJOR_MONTHS: int = 3            # conduit in >= 3 months -> major penalty
