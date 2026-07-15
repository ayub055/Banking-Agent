"""Business-rule thresholds for bureau key findings and risk annotations.

All numeric cut-offs live here so a risk analyst can tune the decisioning
logic in a single place without touching pipeline code.  Both the
deterministic KeyFindings engine (pipeline/key_findings.py) and the LLM
prompt-annotation helper (pipeline/report_summary_chain.py) import from
this module — keeping the numbers consistent across both paths.
"""

# ---------------------------------------------------------------------------
# DPD (Days Past Due)
# ---------------------------------------------------------------------------
DPD_HIGH_RISK: int = 90       # max_dpd > 90  → severe delinquency / NPA risk
DPD_MODERATE_RISK: int = 30   # max_dpd > 30  → significant past-due

# ---------------------------------------------------------------------------
# Credit Card Utilization  (expressed as raw %; 0–100)
# ---------------------------------------------------------------------------
CC_UTIL_HIGH_RISK: int = 75       # util > 75 % → over-utilization
CC_UTIL_MODERATE_RISK: int = 50   # util > 50 % → elevated
CC_UTIL_HEALTHY: int = 30         # util ≤ 30 % → disciplined usage

# ---------------------------------------------------------------------------
# Portfolio Composition
# ---------------------------------------------------------------------------
UNSECURED_PCT_MODERATE_RISK: int = 80   # > 80 % unsecured sanction → heavy skew
UNSECURED_PCT_CONCERN: int = 50          # > 50 % unsecured → majority
OUTSTANDING_PCT_CONCERN: int = 80        # outstanding > 80 % of sanctioned
PRODUCT_DIVERSITY_NEUTRAL: int = 4       # ≥ 4 loan types → diversified portfolio

# ---------------------------------------------------------------------------
# Personal Loan (PL) Activity
# ---------------------------------------------------------------------------
NEW_PL_6M_HIGH_RISK: int = 3      # ≥ 3 new PLs in 6 M → rapid acquisition
NEW_PL_6M_MODERATE_RISK: int = 2  # ≥ 2 new PLs in 6 M → multiple recent PLs
MONTHS_SINCE_TRADE_CONCERN: int = 2  # < 2 months → very recent credit activity

# ---------------------------------------------------------------------------
# Payment Behavior
# ---------------------------------------------------------------------------
MISSED_PAYMENTS_HIGH_RISK: float = 10.0  # > 10 % missed payments (18 M window)
GOOD_CLOSURE_POSITIVE: float = 0.8       # ≥ 80 % good PL closures → strong track record
GOOD_CLOSURE_HIGH_RISK: float = 0.5      # < 50 % good PL closures → poor history
GOOD_CLOSURE_CONCERN: float = 0.7        # < 70 % good PL closures → below average

# ---------------------------------------------------------------------------
# PL Balance Remaining  (% of original sanction still outstanding)
# ---------------------------------------------------------------------------
PL_BAL_REMAINING_HIGH_RISK: int = 80       # > 80 % → limited repayment progress
PL_BAL_REMAINING_MODERATE_RISK: int = 50   # > 50 % → significant balance left (annotations)
PL_BAL_REMAINING_POSITIVE: int = 30        # ≤ 30 % → good progress

# ---------------------------------------------------------------------------
# Enquiry Behavior  (last 12 months, unsecured)
# ---------------------------------------------------------------------------
ENQUIRY_HIGH_RISK: int = 15       # > 15 enquiries → very high pressure
ENQUIRY_MODERATE_RISK: int = 10   # > 10 enquiries → elevated pressure
ENQUIRY_HEALTHY: int = 3          # ≤  3 enquiries → minimal / stable

# ---------------------------------------------------------------------------
# Trade-to-Enquiry Conversion Ratio  (unsecured, 24 M window; stored as %)
# ---------------------------------------------------------------------------
TRADE_RATIO_CONCERN: int = 20    # < 20 % → low conversion / possible rejections
TRADE_RATIO_POSITIVE: int = 50   # > 50 % → high lender acceptance rate

# ---------------------------------------------------------------------------
# Loan Acquisition Velocity — Inter-Purchase Time  (months)
# ---------------------------------------------------------------------------
IPT_HIGH_RISK: float = 1.0    # < 1 month  → rapid loan stacking
IPT_CONCERN: float = 2.0      # < 2 months → frequent acquisitions
IPT_HEALTHY: float = 6.0      # ≥ 6 months → measured, unhurried pace

# ---------------------------------------------------------------------------
# Composite Signal Triggers  (two-signal interaction flags)
# ---------------------------------------------------------------------------
COMPOSITE_ENQUIRY_THRESHOLD: int = 10    # enquiries exceed this → stacking composite
COMPOSITE_NEW_PL_TRIGGER: int = 2        # new PLs ≥ this → stacking composite
COMPOSITE_UTIL_LEVERAGE: int = 50        # CC util > this (%) → leverage composite
COMPOSITE_BAL_LEVERAGE: int = 50         # PL balance > this (%) → leverage composite
COMPOSITE_TRADE_RATIO_LOW: int = 30      # trade_ratio < this → low-conversion flag

# ---------------------------------------------------------------------------
# Bureau Annotation — Clean-History Windows  (used in prompt annotation only)
# ---------------------------------------------------------------------------
CLEAN_HISTORY_STRONG_MONTHS: int = 24   # ≥ 24 M without delinquency → [POSITIVE]
CLEAN_HISTORY_GOOD_MONTHS: int = 12     # ≥ 12 M without delinquency → [POSITIVE]
RECENT_DELINQUENCY_MONTHS: int = 6      # < 6 M → [CONCERN] recent delinquency
PCT_0PLUS_HIGH_RISK: float = 10.0       # > 10 % of trades have 0+ DPD → [HIGH RISK]

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
# Persona Classification Thresholds
# ---------------------------------------------------------------------------
PERSONA_BL_LARGE_AVG_SANCTION: float = 50_00_000    # 50L — Large Business avg sanction
PERSONA_BL_SME_MIN_SANCTION: float = 25_00_000      # 25L — SME minimum total
PERSONA_BL_MICRO_MAX: float = 2_00_000              # 2L — Micro/shopkeeper ceiling
PERSONA_HL_MATURE_SANCTION: float = 50_00_000       # 50L — Mature Salaried HL
PERSONA_HL_METRO_SANCTION: float = 75_00_000        # 75L — Metro Senior
PERSONA_HL_AFFORDABLE_MAX: float = 20_00_000        # 20L — Affordable housing
PERSONA_PL_ENTRY_MAX: float = 5_00_000              # 5L — Entry Salaried PL ceiling
PERSONA_OD_SALARY_MAX: float = 5_00_000             # 5L — Salary OD vs Business OD
PERSONA_GOLD_STRESS_MIN: float = 50_000             # 50K — Gold Loan stress trigger
PERSONA_GOLD_HIGH_STRESS: float = 2_00_000          # 2L — High Asset Stress
PERSONA_CV_FLEET_MIN_COUNT: int = 4                 # Fleet Owner minimum CV count
PERSONA_AL_CLUSTER_MIN: int = 3                     # AL cluster for cab/taxi proxy
PERSONA_BL_LARGE_MIN_COUNT: int = 3                 # Large Business minimum BL count

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
