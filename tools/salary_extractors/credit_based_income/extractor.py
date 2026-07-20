""" Credit-Based Income Extractor """

import logging
from typing import List, Optional

import duckdb
import numpy as np
import pandas as pd
from fuzzywuzzy import fuzz

from tools.salary_extractors.rg_income.extractor import standardize_columns

logger = logging.getLogger("credit_based_income")

# ─── Tunable parameters (revised 25-Aug-2025 where noted) ───────────────────

PARAMS = {
    "window_months": 12,          # trailing window aggregated
    "recent_months": 3,           # months used for the income estimate
    "pos_net_flow_floor": 500,    # a month is "cash-positive" above this net flow
    "median_band_low": 0.6,       # "typical income" band around the median …
    "median_band_high": 1.4,      # … (±40%)
    "salaried_max_txns": 10,      # strict salaried month: < this many credits
    "salaried_max_txns_loose": 20,
    "business_min_txns": 10,      # business month: ≥ this many credits
    "upi_min_txns": 30,           # a month must have > this many UPI credits to classify by mix
    "non_upi_min_txns": 10,       # non-UPI business month: > this many non-UPI credits
    "dominant_share": 0.5,        # one credit "dominates" above this share
    "upi_share_high": 0.6,        # UPI-business threshold
    "upi_share_low": 0.4,         # non-UPI-business threshold
    "self_fraction": 0.5,         # self-transfer heavy in ≥ this share of months
    "flag_fraction": 0.7,         # a behaviour holds in ≥ this share of active credit months
    "quarter_fraction": 0.8,      # quarterly earner: ≥ this share of active quarters in-band
    "stabilise_tol": 0.2,         # income months must sit within ±20% of median to trust it
    "earner_year_floor": 10000,   # EARNER: median annual credit above this …
    "earner_quarter_floor": 30000,  # … OR median quarterly credit above this
    "active_streak": 3,           # ACTIVE: consecutive-month streak …
    "active_months": 6,           # … OR total active months
    "earner_min_credit_months": 3,
    "regular_streak": 3,          # regular earner needs a ≥3-month credit streak
    "self_transfer_fuzzy": 70,    # merchant vs account-holder name ratio → self-transfer (matches rg_income)
}

# Base weight per Depth6 segment (fraction of gross credit treated as income).
PROFILE_POINT = {
    "13. Salaried Profile": 0.5,
    "14. Salaried Profile1": 0.4,
    "16. Others": 0.10,
    "17. UPI Business": 0.10,
    "18. Mixed Business": 0.10,
    "19. Non UPI Business": 0.10,
    "20. Others": 0.10,
    "8. Quarterly Earner": 0.10,
    "9. Seasonal Earner": 0.10,
}

# Which income "view" each scored segment uses, and its cap as a fraction of the
# median recent MONTHLY credit (quarterly/seasonal cap against the median QUARTER).
SEGMENT_INCOME = {
    "13. Salaried Profile": ("sal", 0.80),
    "14. Salaried Profile1": ("sal", 0.75),
    "17. UPI Business": ("upi", 0.50),
    "18. Mixed Business": ("upi", 0.50),
    "19. Non UPI Business": ("non_upi", 0.50),
    "16. Others": ("other", 0.50),
    "20. Others": ("other", 0.50),
    "8. Quarterly Earner": ("non_reg", 0.50),
    "9. Seasonal Earner": ("non_reg", 0.50),
}

# ─── SQL fragments ───────────────────────
# Internal sweep transactions — excluded from every aggregate.
_SWEEP_SQL = """
    CASE
        WHEN tp ILIKE 'SWEEP %'   THEN 1
        WHEN tp ILIKE '%SWEEP%'   THEN 1
        WHEN tp ILIKE '% SWEEP %' THEN 1
        ELSE 0
    END
"""

# Loan-disbursement detection — such credits are borrowings, not income.
_LOAN_SQL = """
    CASE
        WHEN tp ILIKE '% loan %'                        THEN 1
        WHEN tp ILIKE '%/loan%'                         THEN 1
        WHEN tp ILIKE '%finan%'                         THEN 1
        WHEN tp ILIKE '%fincap%'                        THEN 1
        WHEN tp ILIKE '%finser%'                        THEN 1
        WHEN tp ILIKE '%LOS PL%'                        THEN 1
        WHEN tp ILIKE '%LOM PL%'                        THEN 1
        WHEN tp ILIKE '%WAC PL%'                        THEN 1
        WHEN tp ILIKE '%PLCC%'                          THEN 1
        WHEN tp ILIKE '%MOBILE BANKING PL%'             THEN 1
        WHEN tp ILIKE '%HDFC DISB FUNDED HDFC%'         THEN 1
        WHEN tp ILIKE '%ICICI BANK LTD RAOG NEFT DISB%' THEN 1
        WHEN tp ILIKE '%FULLERTON INDIA CREDIT COMP%'   THEN 1
        WHEN tp ILIKE '%RA-LOAN DISBURSEMENT A/C%'      THEN 1
        WHEN tp ILIKE '%LOAN FROM CLIX%'                THEN 1
        WHEN tp ILIKE '%PL DISBURSEMENT SUSP%'          THEN 1
        WHEN tp ILIKE '%RA DISBURSEMENT A/C%'           THEN 1
        WHEN tp ILIKE '%MYLOANCARE VENTURES%'           THEN 1
        WHEN tp ILIKE '%DISB%'                          THEN 1
        ELSE 0
    END
"""

# Payment rail — only UPI is distinguished (reversals tagged UPI_REV, else NULL).
_TRAN_MODE_SQL = """
    CASE
        WHEN tp ILIKE '%UPI%' THEN
            CASE WHEN tp ILIKE 'REV%' THEN 'UPI_REV' ELSE 'UPI' END
    END
"""

# Counterparty (MERCHANT) extraction. Snowflake string
# funcs mapped to DuckDB: charindex→strpos, len→length, RIGHT/SUBSTRING/
# SPLIT_PART/REPLACE unchanged. The result feeds ONLY the self-transfer name
# match, so best-effort parity is sufficient.
_MERCHANT_SQL = """
    CASE
        WHEN tp ILIKE '%UPI%' THEN
            CASE
                WHEN tp ILIKE 'UPI/%'            THEN SPLIT_PART(tp, '/', 2)
                WHEN tp ILIKE 'UPI_CRADJ%'       THEN NULL
                WHEN tp ILIKE 'UPI-REMI-FAILED%' THEN NULL
                WHEN tp ILIKE 'REV-UPI/%'        THEN SPLIT_PART(tp, '/', 2)
                WHEN tp ILIKE 'UPI_RET/%'        THEN NULL
                ELSE NULL
            END
        WHEN tp ILIKE '%NEFT%' THEN
            CASE
                WHEN tp ILIKE 'NEFT %'      THEN SUBSTRING(RIGHT(tp, length(tp) - 5), strpos(RIGHT(tp, length(tp) - 5), ' ') + 1)
                WHEN tp ILIKE '%NEFT/RTGS%' THEN SPLIT_PART(tp, 'NEFT/RTGS', 1)
                WHEN tp ILIKE 'IFT-%'       THEN REPLACE(REPLACE(REPLACE(tp, 'RTGS', ''), 'NEFT', ''), 'IFT-', '')
                WHEN tp ILIKE 'Sent NEFT%'  THEN SPLIT_PART(tp, '/', 2)
                WHEN tp ILIKE '%SENT NEFT %' THEN SPLIT_PART(tp, 'SENT NEFT ', 2)
                WHEN tp ILIKE '%SENT NEFT/%' THEN SPLIT_PART(tp, '/', 2)
                WHEN tp ILIKE 'NEFT-%'      THEN SPLIT_PART(tp, '-', 2)
                WHEN tp ILIKE 'MB:NEFT %'   THEN REPLACE(tp, 'MB:NEFT ', '')
                ELSE NULL
            END
        WHEN tp ILIKE '%IMPS%' THEN
            CASE
                WHEN tp ILIKE 'IMPS %'         THEN RIGHT(tp, length(tp) - 5)
                WHEN tp ILIKE 'Recd:IMPS%'     THEN SPLIT_PART(tp, '/', 3)
                WHEN tp ILIKE 'IMPS-BENETO%'   THEN SPLIT_PART(tp, '-', 4)
                WHEN tp ILIKE 'IMPSBENETO%'    THEN SPLIT_PART(tp, '-', 3)
                WHEN tp ILIKE 'REV Chrg:IMPS%' THEN NULL
                WHEN tp ILIKE 'IMPS-%'         THEN REPLACE(tp, 'IMPS-', '')
                WHEN tp ILIKE '%SENTIMPS%'     THEN REPLACE(SPLIT_PART(tp, '/', 1), 'SentIMPS', '')
                ELSE NULL
            END
        WHEN tp ILIKE '%RTGS%' THEN
            CASE
                WHEN tp ILIKE 'RTGS %'         THEN SUBSTRING(RIGHT(tp, length(tp) - 5), strpos(RIGHT(tp, length(tp) - 5), ' '))
                WHEN tp ILIKE 'MB:SENT RTGS %' THEN REPLACE(tp, 'MB:SENT RTGS ', '')
                WHEN tp ILIKE 'BRB:Sent RTGS %' THEN SPLIT_PART(tp, '/', 2)
                WHEN tp ILIKE 'SENT RTGS%'     THEN SPLIT_PART(tp, '/', 2)
                WHEN tp ILIKE 'RTGS-%'         THEN SPLIT_PART(tp, '-', 2)
                WHEN tp ILIKE 'IB:Sent RTGS %' THEN SPLIT_PART(tp, '/', 2)
                ELSE NULL
            END
        WHEN tp ILIKE '%IFT-%'             THEN SPLIT_PART(tp, '-', 2)
        WHEN tp ILIKE '%NACH-%'            THEN SPLIT_PART(tp, '-', 4)
        WHEN tp ILIKE 'IB:RECEIVED FROM%'  THEN TRIM(SPLIT_PART(tp, 'FROM', 2))
        WHEN tp ILIKE 'FUND TRF FROM%'     THEN TRIM(SPLIT_PART(tp, 'FROM', 2))
        WHEN tp ILIKE 'FT FROM%'           THEN TRIM(SPLIT_PART(tp, 'FROM', 2))
        WHEN tp ILIKE 'FUNDS TRF FROM%'    THEN TRIM(SPLIT_PART(tp, 'FROM', 2))
        WHEN tp ILIKE '%MB:RECEIVED FROM%' THEN TRIM(SPLIT_PART(tp, 'FROM', 2))
        WHEN tp ILIKE 'MB:SENT TO %'       THEN REPLACE(tp, 'MB:SENT TO ', '')
        WHEN tp ILIKE '%IB:FUND%'          THEN TRIM(SPLIT_PART(tp, 'FROM', 2))
        WHEN tp ILIKE 'MB:%'               THEN REPLACE(tp, 'MB:', '')
        WHEN tp ILIKE 'IB:%'               THEN REPLACE(tp, 'IB:', '')
        WHEN tp ILIKE 'CASH DEPOSIT BY %'  THEN REPLACE(tp, 'CASH DEPOSIT BY ', '')
        WHEN tp ILIKE 'ATL/%'              THEN 'CASH_TRANSACTION'
        WHEN tp ILIKE 'ATW/%'              THEN 'CASH_TRANSACTION'
        WHEN tp ILIKE '%SWEEP%'            THEN 'SWEEP'
        WHEN tp ILIKE 'PCD/%'              THEN SPLIT_PART(tp, '/', 3)
        WHEN tp ILIKE 'PG %'               THEN REPLACE(tp, 'PG ', '')
        WHEN tp ILIKE 'CLG TO %'           THEN REPLACE(tp, 'CLG TO ', '')
    END
"""

# Self-transfer narration keywords (force the flag regardless of name match).
_SELF_KEYWORD_SQL = """
        WHEN tp ILIKE '% OWN %'    THEN 1
        WHEN tp ILIKE '% SELF %'   THEN 1
        WHEN tp ILIKE '%/SELF %'   THEN 1
        WHEN tp ILIKE '%/MYSELF%'  THEN 1
        WHEN tp ILIKE '% MYSELF %' THEN 1
"""

# Per-month metrics emitted by the single aggregation pass. Each is generated for
# months 1..12 → collapses B1–B4's four windows into one GROUP BY.
_AGG_TEMPLATES = [
    ("sum_txn_amount_CR",     "sum(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'C' THEN tran_amt END)"),
    ("sum_st_amt_CR",         "sum(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'C' AND stf = 1 THEN tran_amt END)"),
    ("sum_txn_amount_DR",     "sum(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'D' THEN tran_amt END)"),
    ("sum_st_amt_DR",         "sum(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'D' AND stf = 1 THEN tran_amt END)"),
    ("txn_count_CR",          "nullif(count(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'C' THEN 1 END), 0)"),
    ("txn_count_DR",          "nullif(count(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'D' THEN 1 END), 0)"),
    ("max_txn_amount_CR",     "max(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'C' THEN tran_amt END)"),
    ("sum_txn_amount_CR_UPI", "sum(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'C' AND tran_mode = 'UPI' THEN tran_amt END)"),
    ("txn_count_CR_UPI",      "nullif(count(CASE WHEN diff_open_tranx = {m} AND cr_dr = 'C' AND tran_mode = 'UPI' THEN 1 END), 0)"),
]


def _name_match_ratio(a: Optional[str], b: Optional[str]) -> int:
    """fuzzywuzzy ``partial_ratio`` (0–100) of two normalised names.

    Replaces the notebooks' Snowflake ``DIFFERENCE(merchant1, prty_name) >= 4``
    self-transfer test with the same fuzzy name match the sibling extractor uses
    in ``rg_income._exclude_self_transfers`` — keeping both extractors' "is this
    the customer's own name?" logic consistent."""
    if a is None or b is None:
        return 0
    na = "".join(ch for ch in str(a).lower() if ch.isalnum())
    nb = "".join(ch for ch in str(b).lower() if ch.isalnum())
    if not na or not nb:
        return 0
    return int(fuzz.partial_ratio(na, nb))


def _hround(x):
    """Half-away-from-zero rounding, matching Snowflake ROUND (numpy uses
    banker's rounding, which differs on exact .5 cases)."""
    return np.floor(np.asarray(x, dtype=float) + 0.5)


class CreditBasedIncomeExtractor:
    """CC Based income extractor """

    def __init__(self):
        self.conn = duckdb.connect()
        self.conn.create_function(
            "name_match_ratio", _name_match_ratio, ["VARCHAR", "VARCHAR"], "INTEGER")

    def close(self):
        self.conn.close()

    # ─── Public entry point ────────────────────────────────────────────────

    def extract(self, df: Optional[pd.DataFrame] = None, input_csv: Optional[str] = None, cust_id_filter: Optional[List[str]] = None,) -> pd.DataFrame:
        """Return one segment+income record per customer.
        Columns: ``cust_id, depth1..depth6, income_view, multiplier,
        final_income, flag_3m_sum``.
        """
        if df is not None: df = df.copy()
        elif input_csv is not None: df = pd.read_csv(input_csv)
        else: raise ValueError("extract() requires either `df` or `input_csv`")

        df = standardize_columns(df)
        monthly = self._aggregate_months(df, cust_id_filter)
        if monthly.empty: return _empty_result()
        feats = self._derive_features(monthly)
        return self._segment_and_income(feats)

    # ─── Phase A — DuckDB: base transform + 12-month aggregation ────────────

    def _aggregate_months(self, df: pd.DataFrame, cust_ids) -> pd.DataFrame:
        """Replaces B1–B4: one pass producing the wide per-customer monthly frame."""
        self.conn.register("raw_data", df)

        cust_clause = ""
        if cust_ids:
            ids = ", ".join(f"'{c}'" for c in cust_ids)
            cust_clause = f"AND CAST(CUST_ID AS VARCHAR) IN ({ids})"

        # Anchor = latest transaction month; +1 offsets diff so month 1 is the
        # latest month (the notebooks run with business_date one month ahead).
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE base_data AS
            WITH src AS (
                SELECT
                    CUST_ID                         AS cust_id,
                    CAST(TRAN_DATE AS DATE)         AS tran_date,
                    TRAN_AMT_IN_AC                  AS tran_amt,
                    TRAN_PARTCLR                    AS tp,
                    DR_CR_INDCTOR                   AS cr_dr,
                    PRTY_NAME                       AS prty_name
                FROM raw_data
                WHERE current_flag = 'Y' AND del_flag = 'N'
                  {cust_clause}
            )
            SELECT *,
                date_diff('month', last_day(tran_date),
                          last_day((SELECT max(tran_date) FROM src))) + 1 AS diff_open_tranx,
                {_TRAN_MODE_SQL} AS tran_mode,
                {_SWEEP_SQL}     AS sweep_flag,
                {_LOAN_SQL}      AS loan_tag
            FROM src
        """)

        # Trim to the 12-month window up front.
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE windowed AS
            SELECT * FROM base_data
            WHERE diff_open_tranx BETWEEN 1 AND {PARAMS['window_months']}
        """)

        # Merchant name, then self-transfer flag (fuzzy name match OR keywords).
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE flagged AS
            WITH m AS (
                SELECT *, {_MERCHANT_SQL} AS merchant1 FROM windowed
            )
            SELECT *,
                CASE
                    WHEN name_match_ratio(merchant1, prty_name) >= {PARAMS['self_transfer_fuzzy']} THEN 1
                    {_SELF_KEYWORD_SQL}
                    ELSE 0
                END AS stf
            FROM m
        """)

        # One row per customer with all 12 months of every metric.
        agg_cols = ", ".join(
            f"{expr.format(m=m)} AS {name}_{m:02d}M"
            for m in range(1, PARAMS["window_months"] + 1)
            for name, expr in _AGG_TEMPLATES
        )
        return self.conn.execute(f"""
            SELECT cust_id, {agg_cols}
            FROM flagged
            WHERE (cr_dr = 'D' AND sweep_flag = 0)
               OR (cr_dr = 'C' AND loan_tag = 0 AND sweep_flag = 0)
            GROUP BY cust_id
        """).df()

    # ─── Phase B derived features (replaces C) ────────────────────
    def _derive_features(self, monthly: pd.DataFrame) -> dict:
        """Build the numpy matrices + per-customer feature vectors C computes."""
        n = PARAMS["window_months"]

        def mat(metric: str) -> np.ndarray:
            return monthly[[f"{metric}_{m:02d}M" for m in range(1, n + 1)]].to_numpy(dtype=float)

        cr, dr = mat("sum_txn_amount_CR"), mat("sum_txn_amount_DR")
        stcr, stdr = mat("sum_st_amt_CR"), mat("sum_st_amt_DR")
        ccr, cdr = mat("txn_count_CR"), mat("txn_count_DR")
        maxc = mat("max_txn_amount_CR")
        upi, cupi = mat("sum_txn_amount_CR_UPI"), mat("txn_count_CR_UPI")

        # Activity flags per month (NULL count → NaN → not active).
        cr_flag = np.nan_to_num(ccr, nan=0.0) > 0
        dr_flag = np.nan_to_num(cdr, nan=0.0) > 0
        any_flag = (np.nan_to_num(ccr, nan=0.0) + np.nan_to_num(cdr, nan=0.0)) > 0

        def streak(flag: np.ndarray) -> np.ndarray:
            # Consecutive run from month 1 = cumulative AND from column 0.
            return np.cumprod(flag.astype(int), axis=1).sum(axis=1)

        # Quarterly credit totals (coalesce NaN→0, exactly like C's coalesce sum).
        crc = np.nan_to_num(cr, nan=0.0)
        qmat = np.column_stack([crc[:, 0:3].sum(1), crc[:, 3:6].sum(1),
                                crc[:, 6:9].sum(1), crc[:, 9:12].sum(1)])

        crdr = cr - dr                     # NaN where either side NULL (matches SQL)
        pos = crdr > PARAMS["pos_net_flow_floor"]

        with np.errstate(all="ignore"):
            med_Y = np.nanmedian(np.where(np.isnan(cr), np.nan, cr), axis=1)
            mean_Y = np.nanmean(cr, axis=1)
            med_Q = np.median(qmat, axis=1)      # over 4 coalesced quarters
            mean_Q = np.mean(qmat, axis=1)

        return dict(
            monthly=monthly, cr=cr, dr=dr, stcr=stcr, stdr=stdr, ccr=ccr, cdr=cdr,
            maxc=maxc, upi=upi, cupi=cupi,
            cr_flag=cr_flag, dr_flag=dr_flag, any_flag=any_flag, qmat=qmat,
            crdr=crdr, pos=pos,
            months_any=any_flag.sum(1), months_cr=cr_flag.sum(1), months_dr=dr_flag.sum(1),
            streak_any=streak(any_flag), streak_cr=streak(cr_flag), streak_dr=streak(dr_flag),
            pos_months=pos.sum(1), conse_pos=streak(pos),
            med_Y=med_Y, mean_Y=mean_Y, med_Q=med_Q, mean_Q=mean_Q,
        )

    # ─── Phase C — pandas: segmentation + income (replaces D) ───────────────

    def _segment_and_income(self, f: dict) -> pd.DataFrame:
        p = PARAMS
        cr, upi, maxc, ccr, cupi = f["cr"], f["upi"], f["maxc"], f["ccr"], f["cupi"]
        stcr, stdr, dr = f["stcr"], f["stdr"], f["dr"]
        cr_flag, qmat = f["cr_flag"], f["qmat"]
        months_cr, months_dr = f["months_cr"], f["months_dr"]
        med_Y, mean_Y, med_Q = f["med_Y"], f["mean_Y"], f["med_Q"]

        def ratio(a, b):
            with np.errstate(all="ignore"):
                return a / b            # NaN/inf where b is NaN/0 → comparisons False (b guarded by flags)

        medY, medQ = med_Y[:, None], med_Q[:, None]
        r_max = ratio(maxc, cr)
        r_upi = ratio(upi, cr)

        # ── D Step 1/2: monthly pattern counts ──
        months_60 = (cr_flag & (cr >= p["median_band_low"] * medY)
                     & (cr <= p["median_band_high"] * medY)).sum(1)
        st_cr_50 = (cr_flag & (ratio(stcr, cr) > 0.5)).sum(1)
        st_dr_50 = (f["dr_flag"] & (ratio(stdr, dr) > 0.5)).sum(1)
        sal_cnt = (cr_flag & (ccr < p["salaried_max_txns"]) & (r_max > p["dominant_share"])).sum(1)
        sal_cnt1 = (cr_flag & (ccr < p["salaried_max_txns_loose"]) & (r_max > p["dominant_share"])).sum(1)
        bus_cnt = (cr_flag & (ccr >= p["business_min_txns"]) & (r_max <= p["dominant_share"])).sum(1)
        q_within = ((qmat > 0) & (qmat >= p["median_band_low"] * medQ)
                    & (qmat <= p["median_band_high"] * medQ)).sum(1)
        q_cr_cnt = (qmat > 0).sum(1)
        upi_cnt = (cr_flag & (cupi > p["upi_min_txns"]) & (r_upi > p["upi_share_high"])).sum(1)
        mixed_cnt = (cr_flag & (cupi > p["upi_min_txns"]) & (r_upi > p["upi_share_low"])
                     & (r_upi <= p["upi_share_high"])).sum(1)
        nonupi_cnt = (cr_flag & ((ccr - cupi) > p["non_upi_min_txns"])
                      & (r_upi <= p["upi_share_low"])).sum(1)

        # ── D Step 3: behavioural flags ──
        q01, q02 = qmat[:, 0], qmat[:, 1]
        ff = p["flag_fraction"]
        active = (f["streak_any"] >= p["active_streak"]) | (f["months_any"] >= p["active_months"])
        earner = (((med_Y > p["earner_year_floor"]) | (med_Q > p["earner_quarter_floor"]))
                  & (months_cr >= p["earner_min_credit_months"]))
        self_cr = st_cr_50 >= _hround(p["self_fraction"] * months_cr)
        self_dr = st_dr_50 >= _hround(p["self_fraction"] * months_dr)
        regular = (months_60 >= _hround(ff * months_cr)) & (f["streak_cr"] >= p["regular_streak"])
        salaried = sal_cnt >= _hround(ff * months_cr)
        salaried1 = sal_cnt1 >= _hround(ff * months_cr)
        business = bus_cnt >= _hround(ff * months_cr)
        quarter = (q_within >= _hround(p["quarter_fraction"] * q_cr_cnt)) & (q01 > 0) & (q02 > 0)
        seasonal = (q01 > 0) & (q02 > 0)
        upi_flag = upi_cnt >= _hround(ff * months_cr)
        mixed_flag = mixed_cnt >= _hround(ff * months_cr)
        nonupi_flag = nonupi_cnt >= _hround(ff * months_cr)

        # ── D Step 4: Depth1..Depth6 (np.select, first match wins) ──
        non_self = active & earner & ~self_cr
        depth1 = np.where(active, "1. Active", "2. Dormant")
        depth2 = np.select([~active, earner], ["2. Dormant", "3. Earner"], "4. No Earner")
        depth3 = np.select(
            [~active, ~earner, self_cr], ["2. Dormant", "4. No Earner", "5. Majority Self Transfer"],
            "6. Non self Transfer")
        depth4 = np.select(
            [~active, ~earner, self_cr & self_dr, self_cr & ~self_dr,
             non_self & regular, non_self & quarter, non_self & seasonal],
            ["2. Dormant", "4. No Earner", "11. Revolver", "12. Others",
             "7. Regular Earner", "8. Quarterly Earner", "9. Seasonal Earner"],
            "10. Others")
        depth5 = np.select(
            [~active, ~earner, self_cr & self_dr, self_cr & ~self_dr,
             non_self & regular & salaried, non_self & regular & salaried1,
             non_self & regular & business, non_self & regular,
             non_self & quarter, non_self & seasonal],
            ["2. Dormant", "4. No Earner", "11. Revolver", "12. Others",
             "13. Salaried Profile", "14. Salaried Profile1", "15. Business Profile",
             "16. Others", "8. Quarterly Earner", "9. Seasonal Earner"],
            "10. Others")
        depth6 = np.select(
            [~active, ~earner, self_cr & self_dr, self_cr & ~self_dr,
             non_self & regular & salaried, non_self & regular & salaried1,
             non_self & regular & business & upi_flag,
             non_self & regular & business & mixed_flag,
             non_self & regular & business & nonupi_flag,
             non_self & regular & business, non_self & regular,
             non_self & quarter, non_self & seasonal],
            ["2. Dormant", "4. No Earner", "11. Revolver", "12. Others",
             "13. Salaried Profile", "14. Salaried Profile1",
             "17. UPI Business", "18. Mixed Business", "19. Non UPI Business",
             "20. Others", "16. Others", "8. Quarterly Earner", "9. Seasonal Earner"],
            "10. Others")

        # ── D Step 5: multiplier ──
        profile = np.array([PROFILE_POINT.get(d, np.nan) for d in depth6])
        conse, posm = f["conse_pos"].astype(float), f["pos_months"].astype(float)
        cdr_points = np.select(
            [(conse <= 0) & (posm <= 2), (conse <= 0) & (posm <= 5), (conse <= 0) & (posm > 5),
             (conse <= 2) & (posm <= 5), (conse <= 2) & (posm > 5),
             (conse <= 5) & (posm <= 5), (conse <= 5) & (posm > 5),
             (conse > 5) & (posm > 5)],
            [0.0, 0.025, 0.05, 0.05, 0.1, 0.1, 0.15, 0.2], 0.0)
        with np.errstate(all="ignore"):
            gap_Y = np.abs((med_Y - mean_Y) / med_Y)
        mm_point = np.select(
            [(months_cr > 0) & (gap_Y < 0.1), (months_cr > 0) & (gap_Y < 0.2),
             (months_cr > 0) & (gap_Y < 0.4)],
            [0.1, 0.05, 0.025], 0.0)

        is_sal = np.isin(depth6, ["13. Salaried Profile", "14. Salaried Profile1"])
        is_scored = np.isin(depth6, list(SEGMENT_INCOME))
        z = np.nan_to_num
        multiplier = np.where(
            is_sal, z(profile) + z(cdr_points) + z(mm_point),
            np.where(is_scored, z(profile), np.nan))

        # ── D Step 6: raw per-rail income for the 3 recent months / quarters ──
        m = multiplier
        sal_i = maxc[:, :3] * m[:, None] + (cr[:, :3] - maxc[:, :3]) * (m[:, None] / 4)
        upi_i = upi[:, :3] * m[:, None] + (cr[:, :3] - upi[:, :3]) * (m[:, None] / 2)
        nonupi_i = upi[:, :3] * (m[:, None] / 2) + (cr[:, :3] - upi[:, :3]) * m[:, None]
        other_i = cr[:, :3] * m[:, None]
        nonreg_i = qmat[:, :3] * m[:, None]

        # ── D Step 7/8: stabilise (median if 3 months within ±20%, else min) ──
        def stabilise(inc3):
            with np.errstate(all="ignore"):
                med = np.nanmedian(inc3, axis=1)
                mn = np.nanmin(inc3, axis=1)
                within = (np.abs((med[:, None] - inc3) / med[:, None]) < p["stabilise_tol"]).all(axis=1)
            return np.where(med > 0, np.where(within, med, mn), np.nan)

        income = {"sal": stabilise(sal_i), "upi": stabilise(upi_i),
                  "non_upi": stabilise(nonupi_i), "other": stabilise(other_i),
                  "non_reg": stabilise(nonreg_i)}
        with np.errstate(all="ignore"):
            med_credit_month = np.nanmedian(cr[:, :3], axis=1)

        # Select the segment's view and cap it (NaN income → cap, mirroring the
        # SQL where `NULL < cap` is false and falls to the cap branch).
        view = np.array([SEGMENT_INCOME.get(d, (None, None))[0] for d in depth6], dtype=object)
        final = np.full(len(depth6), np.nan)
        for seg, (v, frac) in SEGMENT_INCOME.items():
            sel = depth6 == seg
            if not sel.any():
                continue
            inc = income[v]
            if v == "non_reg":                       # quarterly cap, expressed per-month
                cap = med_Q * frac
                final[sel] = np.where(inc < cap, inc, cap)[sel] / 3
            else:
                cap = med_credit_month * frac
                final[sel] = np.where(inc < cap, inc, cap)[sel]

        # ── D Step 9: plausibility flag over the 3 recent months ──
        flag_3m = (cr[:, :3] > final[:, None]).sum(1)

        return pd.DataFrame({
            "cust_id": f["monthly"]["cust_id"].to_numpy(),
            "depth1": depth1, "depth2": depth2, "depth3": depth3,
            "depth4": depth4, "depth5": depth5, "depth6": depth6,
            "income_view": view, "multiplier": multiplier,
            "final_income": final, "flag_3m_sum": flag_3m,
        })


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "cust_id", "depth1", "depth2", "depth3", "depth4", "depth5", "depth6",
        "income_view", "multiplier", "final_income", "flag_3m_sum",
    ])


def _calculate_credit_based_income(df: Optional[pd.DataFrame] = None,  
                                   input_csv: Optional[str] = None, 
                                   cust_id_filter: Optional[List[str]] = None,) -> pd.DataFrame:
    """Compute credit-based income for every customer in ``df`` (or ``input_csv``).
     Returns one segment+income record per customer.
    """
    ext = CreditBasedIncomeExtractor()
    return ext.extract(df=df, input_csv=input_csv, cust_id_filter=cust_id_filter)
