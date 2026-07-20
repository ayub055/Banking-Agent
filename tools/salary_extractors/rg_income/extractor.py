"""
RG Income Extractor - Main Module
Minimal implementation of RG_INCOME_TAGGER in Python + DuckDB

All configuration is self-contained in this module for maintainability.
"""

import duckdb
import pandas as pd
import logging
from typing import Optional, List
from datetime import datetime, timedelta

# ─── Configuration Constants ────────────────────────────────────────────────

EXCLUSION_KEYWORDS = [
    'PYT LOAN A\\C SPLN', 'MB PL', 'NB PL', 'LOS PL', 'LOM PL', 'WAC PL', 'KW PL', 'PLCC',
    'MOBILE BANKING PL', 'HDFC BANK LTD RA OP', 'HDFC BANK LTD', 'HDFC DISB FUNDED HDFC',
    'BAJAJ FINANCE LTD S', 'BAJAJ FINANCE LTD PAY HDFC', 'ICICI BANK LTD RAOG',
    'ICICI BANK LTD RAOG NEFT DISB', 'FULLERTON INDIA CREDIT COMP', 'RA-LOAN DISBURSEMENT A/C',
    'MUTHOOT FINANCE LIM', 'LOAN FROM CLIX', 'PL DISBURSEMENT SUSP', 'RA DISBURSEMENT A/C',
    'MYLOANCARE VENTURES', 'LOP', 'PPR', 'DISB', 'DIS', 'TATA CAPITAL', 'ADITYABIRLAFINANCEL',
    'STANDARD CHARTERED', 'INDUSND BANK CHENNA', 'INCRED FINANCIAL SERVICES', 'SXFR', 'CDEP',
    'SWEEP', 'CRE001', 'CCPMT', 'DEPBK', 'FINANCE', 'INTEREST', 'FUNDING', 'CASH', 'LOAN', 'REFUND',
    '/OWN', ' OWN ', 'SELF', '/SELF', 'MYSELF', ' NRE ', 'PRINCIPAL', 'CASH DEPOSIT', ' TAX',
    'TD', 'TRF FROM KS', ' P2P ', ' CLG ', ' CHQ ', ' CHARGESWAGES ', ' CHARGES ', ' ADVANCES ',
    'SETTLEMENT', 'ENCASHMENT', ' PMSI ', 'PMSI', 'ADVANCES', 'MUTHOOT'
]

DEFAULT_PARAMS = {
    'min_amount': 10000,
    'lookback_months': 6,
    'min_transactions': 3,
    'max_transactions': 100,
    'min_months_activity': 4,
    'max_sources': 5,
    'max_last_3m_transactions': 20,
    'fuzzy_match_threshold': 3,
    'median_tolerance': 0.20,
    'self_transfer_fuzzy': 70,   # merchant vs account-holder name ratio to treat a source as self-transfer
}

logger = logging.getLogger('rg_income')


# ─── Utility Functions ──────────────────────────────────────────────────────

def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardize column names to expected format.

    Column mapping:
        - cust_id, customer_id, crn, client_id → CUST_ID
        - account_num, acc_num, account_number → ACCNT_NUM
        - party_name, prty_name, customer_name, name → PRTY_NAME
        - tran_date, transaction_date, txn_date, date → TRAN_DATE
        - tran_amt, amount, txn_amt, tran_amt_in_ac → TRAN_AMT_IN_AC
        - tran_partclr, particulars, narration, description, desc → TRAN_PARTCLR
        - dr_cr_ind, debit_credit, dr_cr_indctor → DR_CR_INDCTOR
        - del_flag, delete_flag → DEL_FLAG
        - current_flag → CURRENT_FLAG

    Args:
        df: Input DataFrame with any column naming convention

    Returns:
        DataFrame with standardized column names
    """
    # Create a copy to avoid modifying the original
    df = df.copy()

    # Column name mapping (lowercase input → standardized output)
    COLUMN_MAPPING = {
        'cust_id': 'CUST_ID',  # Customer ID variations
        'customer_id': 'CUST_ID',
        'crn': 'CUST_ID',
        'account_num': 'ACCNT_NUM',  # Account number variations
        'acc_num': 'ACCNT_NUM',
        'account_number': 'ACCNT_NUM',
        'accnt_num': 'ACCNT_NUM',
        'party_name': 'PRTY_NAME', # Party/Customer name variations
        'prty_name': 'PRTY_NAME',
        'customer_name': 'PRTY_NAME',
        'cust_name': 'PRTY_NAME',
        'tran_date': 'TRAN_DATE', # Transaction date variations
        'transaction_date': 'TRAN_DATE',
        'txn_date': 'TRAN_DATE',
        'date': 'TRAN_DATE',
        'tran_amt': 'TRAN_AMT_IN_AC', # Transaction amount variations
        'amount': 'TRAN_AMT_IN_AC',
        'txn_amt': 'TRAN_AMT_IN_AC',
        'tran_amt_in_ac': 'TRAN_AMT_IN_AC',
        'tran_partclr': 'TRAN_PARTCLR', # Transaction particulars variations
        'particulars': 'TRAN_PARTCLR',
        'narration': 'TRAN_PARTCLR',
        'description': 'TRAN_PARTCLR',
        'desc': 'TRAN_PARTCLR',
        'txn_desc': 'TRAN_PARTCLR',
        'dr_cr_ind': 'DR_CR_INDCTOR', # Debit/Credit indicator variations
        'debit_credit': 'DR_CR_INDCTOR',
        'dr_cr_indctor': 'DR_CR_INDCTOR',
        'dr_cr_indicator': 'DR_CR_INDCTOR',
        'del_flag': 'DEL_FLAG', # Delete flag variations
        'delete_flag': 'DEL_FLAG',
        'current_flag': 'CURRENT_FLAG', # Current flag variations
        }

    # Convert column names to lowercase for case-insensitive matching
    lower_cols = {col: col.lower() for col in df.columns}
    rename_map = {} # Build rename mapping
    for orig_col, lower_col in lower_cols.items():
        if lower_col in COLUMN_MAPPING: rename_map[orig_col] = COLUMN_MAPPING[lower_col]

    if rename_map: # Apply renaming
        df = df.rename(columns=rename_map)
        logger.info(f"[Standardize] Renamed columns: {rename_map}")

    # Validate required columns exist
    REQUIRED_COLS = ['CUST_ID', 'TRAN_DATE', 'TRAN_AMT_IN_AC', 'TRAN_PARTCLR']
    missing = [col for col in REQUIRED_COLS if col not in df.columns]
    if missing:
        available = list(df.columns)
        raise ValueError(
            f"Missing required columns after standardization: {missing}\n"
            f"Available columns: {available}\n"
            f"Please ensure your data contains: customer ID, transaction date, "
            f"transaction amount, and transaction particulars/narration"
        )

    # Add default flags if missing
    if 'DR_CR_INDCTOR' not in df.columns:
        df['DR_CR_INDCTOR'] = 'C'  # Assume credit transactions
        logger.info("[Standardize] Added default DR_CR_INDCTOR='C'")

    if 'DEL_FLAG' not in df.columns:
        df['DEL_FLAG'] = 'N'  # Assume not deleted
        logger.info("[Standardize] Added default DEL_FLAG='N'")

    if 'CURRENT_FLAG' not in df.columns:
        df['CURRENT_FLAG'] = 'Y'  # Assume current records
        logger.info("[Standardize] Added Added default CURRENT_FLAG='Y'")

    if 'ACCNT_NUM' not in df.columns:
        df['ACCNT_NUM'] = df['CUST_ID']  # Use CUST_ID as fallback
        logger.info("[Standardize] Added ACCNT_NUM from CUST_ID")

    if 'PRTY_NAME' not in df.columns:
        df['PRTY_NAME'] = 'Unknown'  # Default party name
        logger.info("[Standardize] Added default PRTY_NAME='Unknown'")

    return df


class RGIncomeExtractor:
    """Main class for RG Income extraction using DuckDB"""

    def __init__(self, min_amount: int = 10000, lookback_months: int = 6, fuzzy_threshold: int = 3):
        self.params = DEFAULT_PARAMS.copy()
        self.params.update({'min_amount': min_amount, 'lookback_months': lookback_months, 'fuzzy_threshold': fuzzy_threshold})
        self.conn = duckdb.connect()

    def close(self):
        self.conn.close()

    def extract(self,
                input_csv: Optional[str] = None,
                df: Optional[pd.DataFrame] = None,
                cust_id_filter: Optional[List[str]] = None,
                start_date: Optional[str] = None,
                end_date: Optional[str] = None) -> pd.DataFrame:
        """
        Main entry point. Processes transactions and returns RG income results.

        Accepts either an in-memory DataFrame (`df`) or a CSV path (`input_csv`).
        Dates should be in YYYY-MM-DD format; when omitted they are derived from
        the data itself — the trailing `lookback_months` months up to and
        including the latest month present (never anchored to the wall clock).
        """
        if df is not None:
            df = df.copy()
        elif input_csv is not None:
            logger.info(f"Loading data from {input_csv}")
            df = pd.read_csv(input_csv)
        else:
            raise ValueError("extract() requires either `df` or `input_csv`")

        # Standardize column names to expected format
        df = standardize_columns(df)

        # Derive the date window from the data when not supplied: the trailing
        # `lookback_months` months up to and including the latest month present.
        if not start_date or not end_date:
            tran_dates = pd.to_datetime(df['TRAN_DATE'], errors='coerce')
            recent_months = sorted(tran_dates.dt.to_period('M').dropna().unique())[-self.params['lookback_months']:]
            if not start_date:
                start_date = recent_months[0].start_time.strftime('%Y-%m-%d')
            if not end_date:
                end_date = tran_dates.max().strftime('%Y-%m-%d')

        logger.info(f"Date range: {start_date} to {end_date}")

        self._base_table(df, cust_id_filter, start_date, end_date)
        self._extract_merchants()
        self._cluster_merchants()
        return self._calculate_income()

    def get_income_strings(self) -> pd.DataFrame:
        """Return the transaction-level rows behind each detected income source.

        These are the individual credit "strings" (date + narration + amount)
        that make up every income cluster, including salary-classified sources
        (merchant = 'salary'). Built from the internal `income_final` table, so
        it must be called after `extract()`. Nothing consumes this yet — it is
        logged for now, as a report section may need it later (also for salary).
        """
        return self.conn.execute("""
            SELECT DISTINCT
                CUST_ID        AS cust_id,
                ACCNT_NUM      AS accnt_num,
                TRAN_DATE      AS tran_date,
                TRAN_PARTCLR   AS tran_partclr,
                TRAN_AMT_IN_AC AS tran_amt_in_ac,
                merchant,
                cluster_id,
                src_income,
                total_income,
                all_months
            FROM income_final
            ORDER BY cust_id, cluster_id, tran_date
        """).df()

    # ─── Segment 1: Load & filter ──────────────────────────────────────────────

    def _base_table(self, df: pd.DataFrame, cust_ids, start_date: str, end_date: str):
        """Load CSV into DuckDB, apply date/amount/exclusion filters."""
        # Anchor for the "latest 3 months" recency window: the latest
        # transaction month (see _calculate_income → recent_months).
        self._anchor_end_date = end_date
        self.conn.register('raw_data', df)

        exclusions = "\n            AND ".join(
            [f"TRAN_PARTCLR NOT ILIKE '%{kw}%'" for kw in EXCLUSION_KEYWORDS]
        )

        cust_clause = ""
        if cust_ids:
            ids = ", ".join([f"'{c}'" for c in cust_ids])
            cust_clause = f"AND CUST_ID IN ({ids})"

        self.conn.execute(f"""
            CREATE OR REPLACE TABLE base_data AS
            SELECT
                CUST_ID,
                ACCNT_NUM,
                PRTY_NAME AS customer_name,
                CAST(TRAN_DATE AS DATE) AS TRAN_DATE,
                TRAN_AMT_IN_AC,
                TRAN_PARTCLR,
                EXTRACT(YEAR  FROM CAST(TRAN_DATE AS DATE)) AS year,
                -- Year-aware month key (YYYYMM) so recency ordering and
                -- month counting stay correct across a year boundary.
                EXTRACT(YEAR  FROM CAST(TRAN_DATE AS DATE)) * 100
                    + EXTRACT(MONTH FROM CAST(TRAN_DATE AS DATE)) AS month
            FROM raw_data
            WHERE current_flag = 'Y'
              AND DEL_FLAG     = 'N'
              AND DR_CR_INDCTOR = 'C'
              AND TRAN_AMT_IN_AC >= {self.params['min_amount']}
              AND CAST(TRAN_DATE AS DATE) BETWEEN '{start_date}' AND '{end_date}'
              AND {exclusions}
              {cust_clause}
        """)
        cnt = self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT CUST_ID) FROM base_data").fetchone()
        logger.info(f"[S1] base_data: {cnt[0]} rows | {cnt[1]} customers")


    def _extract_merchants(self):
        """Parse TRAN_PARTCLR to extract and clean merchant names.

        NOTE: This logic MUST match the notebook exactly to ensure correct calculations.
        The CASE statement below is transcribed verbatim from RG_INCOME_TAGGER _AUTOMATED_UPD.ipynb
        """

        # Map transaction particulars to merchant names - EXACT match to notebook logic
        self.conn.execute("""
            CREATE OR REPLACE TABLE merchants_raw AS
            SELECT *,
                CASE
                    -- OPTIMIZED: Group salary patterns hierarchically
                    -- Pattern hierarchy: most specific first, then generic
                    WHEN
                        -- Exact/cognizant matches (most specific)
                        TRAN_PARTCLR ILIKE '%cognizantsal%' OR
                        TRAN_PARTCLR ILIKE 'PROFESSIONAL FEE%' OR
                        TRAN_PARTCLR ILIKE 'STIPEND%' OR
                        TRAN_PARTCLR ILIKE '%PROFESSIONALFEE%' OR
                        TRAN_PARTCLR ILIKE '%PROFESSIONAL FEE%' OR
                        -- Payroll/Income bulk patterns
                        TRAN_PARTCLR ILIKE '%payroll%' OR
                        TRAN_PARTCLR ILIKE '%BULK DEPOSIT%' OR
                        TRAN_PARTCLR ILIKE '% INCOME %' OR
                        -- Salary variations (alphabetical for readability)
                        TRAN_PARTCLR ILIKE 'salary%' OR
                        TRAN_PARTCLR ILIKE '%SALARY%' OR
                        TRAN_PARTCLR ILIKE '% SALARY %' OR
                        TRAN_PARTCLR ILIKE '%/salary%' OR
                        TRAN_PARTCLR ILIKE '%/salary' OR
                        TRAN_PARTCLR ILIKE '%/ salary%' OR
                        TRAN_PARTCLR ILIKE '%/salar' OR
                        TRAN_PARTCLR ILIKE '%/ salar%' OR
                        TRAN_PARTCLR ILIKE '%salar%' OR
                        -- Short forms with delimiters
                        TRAN_PARTCLR ILIKE 'sal %' OR
                        TRAN_PARTCLR ILIKE '%/ sal %' OR
                        TRAN_PARTCLR ILIKE '%/sal %' OR
                        TRAN_PARTCLR ILIKE '%-sal' OR
                        TRAN_PARTCLR ILIKE '%-sal-%' OR
                        -- Wage patterns
                        TRAN_PARTCLR ILIKE '%WAGE%' OR
                        TRAN_PARTCLR ILIKE '%WAGES%'
                        THEN 'SALARY'
                    WHEN TRAN_PARTCLR ILIKE '%IFT%' THEN
                        CASE
                            WHEN TRAN_PARTCLR ILIKE '%IFT-%' THEN SPLIT_PART(TRAN_PARTCLR, '-', 2)
                            ELSE TRIM(REGEXP_REPLACE(TRAN_PARTCLR, 'IFT\\s*', ''))
                        END
                    WHEN TRAN_PARTCLR ILIKE 'FROM %' THEN
                        TRIM(
                            SPLIT_PART(TRAN_PARTCLR, ' ', 2) ||
                            COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 3), '') ||
                            COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 4), '')
                        )
                    WHEN TRAN_PARTCLR ILIKE '%NACH%' THEN
                        CASE
                             WHEN TRAN_PARTCLR ILIKE '%NACH-%' THEN SPLIT_PART(TRAN_PARTCLR, '-', 4)
                             ELSE TRIM(REGEXP_REPLACE(TRAN_PARTCLR, 'NACH\\s*', ''))
                        END
                    -- NOTE: Pattern order matters - evaluate specific patterns before generic ones
                    -- Group 1: "FROM" extraction patterns (all use same SPLIT_PART logic)
                    -- OPTIMIZATION: Could consider consolidating these if logic identical
                    WHEN TRAN_PARTCLR ILIKE 'IB:RECEIVED FROM%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, 'FROM', 2))
                    WHEN TRAN_PARTCLR ILIKE 'FUND TRF FROM%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, 'FROM', 2))
                    WHEN TRAN_PARTCLR ILIKE 'FUNDS TRF FROM%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, 'FROM', 2))
                    WHEN TRAN_PARTCLR ILIKE 'FT FROM%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, 'FROM', 2))
                    WHEN TRAN_PARTCLR ILIKE '%UPI%' THEN
                        CASE
                             WHEN TRAN_PARTCLR ILIKE '%UPI/%' THEN SPLIT_PART(TRAN_PARTCLR, '/', 2)
                             ELSE TRIM(REGEXP_REPLACE(TRAN_PARTCLR, 'UPI\\s*', ''))
                        END
                    WHEN TRAN_PARTCLR ILIKE '%IMPS%' THEN
                        CASE
                            WHEN TRAN_PARTCLR ILIKE '%IMPS/%' THEN SPLIT_PART(TRAN_PARTCLR, '/', 3)
                            ELSE TRIM(REGEXP_REPLACE(TRAN_PARTCLR, 'IMPS\\s*', ''))
                        END
                    WHEN TRAN_PARTCLR ILIKE '%MB:RECEIVED FROM%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, 'FROM', 2))
                    WHEN TRAN_PARTCLR ILIKE '%IB:FUND%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, 'FROM', 2))
                    WHEN TRAN_PARTCLR ILIKE '%NEFT%' THEN
                        CASE
                            WHEN TRAN_PARTCLR ILIKE '%NEFT %' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, ' ', 3) ||
                                COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 4), '') ||
                                COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 5), '') ||
                                COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 6), ''))
                            ELSE TRIM(REGEXP_REPLACE(TRAN_PARTCLR, 'NEFT\\s*', ''))
                        END
                    WHEN TRAN_PARTCLR ILIKE '%IB: FUND TRANSFER%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, ' ', 5) ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 4), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 6), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 7), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 8), ''))
                    WHEN TRAN_PARTCLR ILIKE '%FUND TRANSFER FROM%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, ' ', 5) ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 4), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 6), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 7), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 8), ''))
                    -- NOTE: Position 4 is intentionally duplicated to match notebook exactly
                    WHEN TRAN_PARTCLR ILIKE '%FUNDS TRANSFER FROM%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, ' ', 4) ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 4), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 5), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 6), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 7), ''))
                    WHEN TRAN_PARTCLR ILIKE '%FT FROM%' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, ' ', 3) ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 4), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 5), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 6), '') ||
                        COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 7), ''))
                    WHEN TRAN_PARTCLR ILIKE '%RTGS%' THEN
                        CASE
                            WHEN TRAN_PARTCLR ILIKE '%RTGS %' THEN TRIM(SPLIT_PART(TRAN_PARTCLR, ' ', 3) ||
                                COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 4), '') ||
                                COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 5), '') ||
                                COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 6), '') ||
                                COALESCE(' ' || SPLIT_PART(TRAN_PARTCLR, ' ', 7), ''))
                            ELSE TRIM(REGEXP_REPLACE(TRAN_PARTCLR, 'RTGS\\s*', ''))
                        END
                    ELSE TRIM(REGEXP_REPLACE(TRAN_PARTCLR, '(NEFT|IFT|RTGS|UPI|IMPS|MB):?\\s*', ''))
                END AS remitter
            FROM base_data
        """)

        # Clean: lowercase, strip special chars & payment-method words
        # NOTE: Must match notebook logic exactly - includes 'paymen' pattern
        self.conn.execute("""
            CREATE OR REPLACE TABLE merchants_clean AS
            SELECT *,
                TRIM(REGEXP_REPLACE(
                    LOWER(TRIM(REGEXP_REPLACE(remitter, '[^a-zA-Z\\s]', ' ', 'g'))),
                    '(neft|imps|rtgs|ift|ft|upi|chq|cash|transfer|trf|mb:|payment|paymen)',
                    ' ', 'g'
                )) AS merchant
            FROM merchants_raw
            WHERE remitter IS NOT NULL AND trim(remitter) <> ''
        """)

        # Keep only customers with 3-100 credit txns and >=4 active months
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE merchants_filtered AS
            SELECT m.*
            FROM merchants_clean m
            JOIN (
                SELECT CUST_ID
                FROM merchants_clean
                GROUP BY CUST_ID
                HAVING COUNT(*) BETWEEN {self.params['min_transactions']} AND {self.params['max_transactions']}
                   AND COUNT(DISTINCT month) >= {self.params['min_months_activity']}
            ) valid ON m.CUST_ID = valid.CUST_ID
        """)

        cnt = self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT CUST_ID) FROM merchants_filtered").fetchone()
        logger.info(f"[S2] merchants_filtered: {cnt[0]} rows | {cnt[1]} customers")


    def _cluster_merchants(self):
        """Group similar merchant names using editdist3 (Levenshtein).

        NOTE ON FUZZY MATCHING ALGORITHM DIFFERENCE:
        - Original notebook uses Redshift DIFFERENCE() (SOUNDEX-based, returns 0-4)
        - Python implementation uses DuckDB editdist3() (Levenshtein distance)
        - These are NOT equivalent algorithms!
        - DIFFERENCE > 3 in SOUNDEX means high phonetic similarity
        - editdist3 <= threshold means character-level edit distance
        - Threshold calibration may be needed to match original behavior
        - Current threshold (3) is an approximation; adjust if clustering differs
        """
        # TODO: Consider calibrating threshold by comparing outputs with original notebook

        # One row per (CUST_ID, merchant) with a stable row ID.
        # NOTE: DISTINCT must run BEFORE row_number(), otherwise the window
        # function makes every row unique and the same merchant string ends up
        # with many uids — which both fans out the `clustered` join and prevents
        # identical merchants from ever clustering together.
        self.conn.execute("""
            CREATE OR REPLACE TABLE uniq_merchants AS
            SELECT CUST_ID, merchant,
                   row_number() OVER (ORDER BY CUST_ID, merchant) AS uid
            FROM (
                SELECT DISTINCT CUST_ID, merchant
                FROM merchants_filtered
                WHERE merchant IS NOT NULL AND length(trim(merchant)) > 1
            ) d
        """)

        # All within-customer pairs that are similar enough, INCLUDING the
        # reflexive self-pair (uid == uid) so a merchant with no near-neighbour
        # still forms its own cluster. Identical merchant strings are already
        # collapsed to one row in uniq_merchants, so this binds near-duplicates.
        threshold = self.params['fuzzy_threshold']
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE similar_pairs AS
            SELECT a.CUST_ID,
                   a.uid   AS uid1, b.uid   AS uid2,
                   a.merchant AS m1,  b.merchant AS m2,
                   editdist3(a.merchant, b.merchant) AS dist
            FROM uniq_merchants a
            JOIN uniq_merchants b
              ON a.CUST_ID = b.CUST_ID AND a.uid <= b.uid
            WHERE editdist3(a.merchant, b.merchant) <= {threshold}
        """)

        # Assign cluster_id = min(uid) in each connected component (union-find via CTE)
        # Simpler but effective: use the smaller uid in each matched pair as cluster root
        self.conn.execute("""
            CREATE OR REPLACE TABLE cluster_map AS
            WITH edges AS (
                SELECT uid1 AS node, uid2 AS root FROM similar_pairs
                UNION ALL
                SELECT uid2 AS node, uid1 AS root FROM similar_pairs
            ),
            roots AS (
                -- Each node maps to the minimum uid in its neighbourhood
                SELECT node, MIN(root) AS cluster_id FROM edges GROUP BY node
            )
            SELECT uid AS node, COALESCE(r.cluster_id, u.uid) AS cluster_id
            FROM uniq_merchants u
            LEFT JOIN roots r ON u.uid = r.node
        """)

        # Attach cluster_id back to transaction-level data.
        # uniq_merchants is now one row per (CUST_ID, merchant), so this join
        # no longer fans out. Unclustered merchants fall back to their own uid.
        self.conn.execute("""
            CREATE OR REPLACE TABLE clustered AS
            SELECT mf.*,
                   COALESCE(cm.cluster_id, um.uid) AS cluster_id
            FROM merchants_filtered mf
            JOIN uniq_merchants um
              ON mf.CUST_ID = um.CUST_ID AND mf.merchant = um.merchant
            JOIN cluster_map cm ON um.uid = cm.node
        """)

        cnt = self.conn.execute("SELECT COUNT(DISTINCT cluster_id), COUNT(DISTINCT CUST_ID) FROM clustered").fetchone()
        logger.info(f"[S3] clustered: {cnt[0]} clusters | {cnt[1]} customers")

    # ─── Segment 4: Income calculation & output ────────────────────────────────

    def _calculate_income(self) -> pd.DataFrame:
        """Validate clusters, calculate src_income/total_income, return result."""

        min_months = self.params['min_months_activity']
        tolerance  = self.params['median_tolerance']
        max_src    = self.params['max_sources']
        max_txn    = self.params['max_last_3m_transactions']

        # Valid clusters = >=4 distinct months
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE valid_clusters AS
            SELECT CUST_ID, cluster_id
            FROM clustered
            GROUP BY CUST_ID, cluster_id
            HAVING COUNT(DISTINCT month) >= {min_months}
        """)

        self.conn.execute("""
            CREATE OR REPLACE TABLE valid_txns AS
            SELECT c.*
            FROM clustered c
            JOIN valid_clusters vc USING (CUST_ID, cluster_id)
        """)

        # The "latest 3 months" = the anchor month (the latest transaction
        # month) and the two calendar months immediately before it, as
        # consecutive, year-aware months (e.g. 202602, 202601, 202512) — NOT
        # the three highest month-of-year numbers. A cluster must appear in all
        # three of these to pass the recency gate (active_clusters below); a
        # gap month with no activity therefore disqualifies the source.
        anchor = pd.Period(self._anchor_end_date, freq='M')
        recent_yyyymm = [(anchor - i).year * 100 + (anchor - i).month for i in range(3)]
        values_sql = ", ".join(f"({m})" for m in recent_yyyymm)
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE recent_months AS
            SELECT * FROM (VALUES {values_sql}) AS t(month)
        """)

        # Flag transactions in last 3 months + compute all_months per cluster
        self.conn.execute("""
            CREATE OR REPLACE TABLE flagged AS
            SELECT v.*,
                CASE WHEN v.month IN (SELECT month FROM recent_months) THEN 1 ELSE 0 END AS last3m_flag
            FROM valid_txns v
        """)

        # Compute all_months as a separate aggregate (string_agg is not a window fn)
        self.conn.execute("""
            CREATE OR REPLACE TABLE cluster_months AS
            SELECT CUST_ID, cluster_id,
                   string_agg(DISTINCT month::VARCHAR, ',' ORDER BY month::VARCHAR) AS all_months
            FROM flagged
            GROUP BY CUST_ID, cluster_id
        """)

        # Keep only clusters that appear in all 3 recent months
        self.conn.execute("""
            CREATE OR REPLACE TABLE active_clusters AS
            SELECT CUST_ID, cluster_id
            FROM flagged
            WHERE last3m_flag = 1
            GROUP BY CUST_ID, cluster_id
            HAVING COUNT(DISTINCT month) = (SELECT COUNT(*) FROM recent_months)
        """)

        self.conn.execute("""
            CREATE OR REPLACE TABLE final_t AS
            SELECT f.*
            FROM flagged f
            JOIN active_clusters ac USING (CUST_ID, cluster_id)
            WHERE f.last3m_flag = 1
        """)

        # Income = median if all monthly sums within ±20% of median, else min
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE income_calc AS
            WITH cluster_monthly AS (
                SELECT CUST_ID, cluster_id, month,
                       SUM(TRAN_AMT_IN_AC) AS monthly_sum
                FROM final_t
                GROUP BY CUST_ID, cluster_id, month
            ),
            cluster_stats AS (
                SELECT CUST_ID, cluster_id,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY monthly_sum) AS median_amt,
                       MIN(monthly_sum) AS min_amt
                FROM cluster_monthly
                GROUP BY CUST_ID, cluster_id
            ),
            stability AS (
                SELECT cm.CUST_ID, cm.cluster_id,
                       MIN(CASE WHEN cm.monthly_sum BETWEEN cs.median_amt * {1 - tolerance}
                                                       AND cs.median_amt * {1 + tolerance}
                                THEN 1 ELSE 0 END) AS all_stable
                FROM cluster_monthly cm
                JOIN cluster_stats cs USING (CUST_ID, cluster_id)
                GROUP BY cm.CUST_ID, cm.cluster_id
            ),
            src AS (
                SELECT cs.CUST_ID, cs.cluster_id,
                       CASE WHEN s.all_stable = 1 THEN cs.median_amt ELSE cs.min_amt END AS src_income
                FROM cluster_stats cs
                JOIN stability s USING (CUST_ID, cluster_id)
            ),
            -- total_income = SUM of src_income over DISTINCT (CUST_ID, cluster_id),
            -- i.e. one income per source. Matches notebook cell-34's total_income CTE.
            -- (Do NOT use SUM(DISTINCT src_income): that collapses two sources that
            --  happen to share the same income amount.)
            cust_total AS (
                SELECT CUST_ID, SUM(src_income) AS total_income
                FROM (SELECT DISTINCT CUST_ID, cluster_id, src_income FROM src) d
                GROUP BY CUST_ID
            )
            SELECT f.*,
                   src.src_income,
                   ct.total_income
            FROM final_t f
            JOIN src USING (CUST_ID, cluster_id)
            JOIN cust_total ct USING (CUST_ID)
        """)

        # Apply final filter: <=5 income sources, <=20 last-3m transactions; attach all_months
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE income_final AS
            SELECT ic.*, cm.all_months
            FROM income_calc ic
            JOIN cluster_months cm USING (CUST_ID, cluster_id)
            JOIN (
                SELECT CUST_ID
                FROM income_calc
                GROUP BY CUST_ID
                HAVING COUNT(DISTINCT cluster_id) <= {max_src}
                   AND COUNT(*) <= {max_txn}
            ) valid ON ic.CUST_ID = valid.CUST_ID
        """)

        # Select final output columns
        result = self.conn.execute("""
            SELECT DISTINCT
                CUST_ID          AS cust_id,
                ACCNT_NUM        AS accnt_num,
                customer_name,
                cluster_id,
                merchant,
                src_income,
                total_income,
                all_months
            FROM income_final
            ORDER BY cust_id, cluster_id
        """).df()

        cnt = self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT CUST_ID) FROM income_final").fetchone()
        logger.info(f"[S4] Final output: {cnt[0]} rows | {cnt[1]} customers")

        # Segment 5: drop sources that are really the customer's own self-transfers
        return self._exclude_self_transfers(result)

    # ─── Segment 5: Self-transfer exclusion ────────────────────────────────────

    def _exclude_self_transfers(self, result: pd.DataFrame) -> pd.DataFrame:
        """Drop income sources that are actually the customer's own self-transfers.

        A source whose extracted `merchant` name fuzzily matches the account
        holder's own name (`customer_name`, i.e. PRTY_NAME) at or above
        `self_transfer_fuzzy` is the customer moving their own money, not income.
        Such sources are removed and `total_income` is reduced by their
        `src_income` (total_income - Σ src_income of the removed sources).

          case 1 — no source matches       → returned unchanged
          case 2 — one/more sources match  → those sources dropped, total re-adjusted
        """
        if result.empty:
            return result

        from fuzzywuzzy import fuzz

        def _norm(s) -> str:
            return ''.join(ch for ch in str(s).lower() if ch.isalnum())

        threshold = self.params['self_transfer_fuzzy']
        # Decide once per source (cluster), not per row — a cluster can carry
        # several near-duplicate merchant strings; flag/drop the whole source.
        # customer_name is constant within a customer, so normalise it here too.
        srcs = result.drop_duplicates(['cust_id', 'cluster_id'])
        self_keys = {
            (r.cust_id, r.cluster_id)
            for r in srcs.itertuples()
            if fuzz.partial_ratio(_norm(r.merchant), _norm(r.customer_name)) >= threshold
        }
        if not self_keys:
            return result   # case 1 — nothing is a self-transfer

        # case 2 — remove self-transfer source(s) and re-adjust total income.
        # Subtract one src_income per distinct (customer, cluster) removed.
        is_self = pd.Series(
            [(c, cl) in self_keys for c, cl in zip(result['cust_id'], result['cluster_id'])],
            index=result.index,
        )
        removed = result[is_self].drop_duplicates(['cust_id', 'cluster_id'])
        per_cust_removed = removed.groupby('cust_id')['src_income'].sum()

        kept = result[~is_self].copy()
        kept['total_income'] = kept['total_income'] - kept['cust_id'].map(per_cust_removed).fillna(0.0)

        for r in removed.itertuples():
            logger.info("[S5] self-transfer excluded: cust=%s merchant=%r src_income=%.0f",
                        r.cust_id, r.merchant, r.src_income)
        return kept.reset_index(drop=True)
