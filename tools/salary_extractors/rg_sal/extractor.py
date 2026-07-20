"""
RG_SAL Extractor — pure-DuckDB port of RG_SAL.ipynb (tools/salary_extractors/rg_sal/rg_sal_sql.txt).

Computes salary/income from credit transactions via THREE sub-methods, then a
hierarchy merge that picks ONE method per customer:

    a. Percent  — a single credit that is >=40% of the month's total credits
    b. Salary   — narration explicitly tagged 'salary'
    c. Company  — narration matches a large employer / company-keyword list

    hierarchy priority (cell 38):  Salary (1) > Percent (2) > Company (3)

This is a VERBATIM SQL transcription (CONVERSION_NOTES.md §5 hard constraints). The
only deviations from the notebook text are:

  1. Anchor-from-data window + relative month index. The notebook is hardcoded to a
     stale window whose month arithmetic only lines up when the 6 window months map to
     calendar indices 1..6 (e.g. Px's TO_CHAR='012026'… mapping, latest_month_flag's
     `month=6`). We derive the trailing `lookback_months` window from max(tran_date) and
     assign each row a YEAR-AWARE relative month index 1..N (dense-rank of (year,month)),
     wiring Px, latest_month_flag and the month_N pivots to it — the same accommodation
     rg_income / credit_based_income already make.
  2. DIFFERENCE(S1,S2) > 3  (merchant↔merchant Soundex clustering, no DuckDB builtin)
     → name_match_ratio(S1,S2) >= 70  (fuzzywuzzy partial_ratio UDF, per §3).
  3. fuzzy_score(name, merchant) > 60  (self-transfer name match, no DuckDB builtin)
     → fuzz.ratio UDF, threshold kept verbatim.
  4. Mechanical de-Redshift (NOT logic): dropped distkey/sortkey and the mod()/NTILE(2)
     two-partition split (collapsed to a single fuzzy pass — semantically identical);
     `sandbox.*` tables → local tables; TO_CHAR('MMYYYY') month mapping → relative index.

The three sub-methods share the base transform, merchant extract/clean, count/month
gate, clustering and the whole refinement step; they differ only in their exclusion
list, the Percent pre-gate, the Company whitelist, the Salary `merchant='salary'` filter
and one merchant-CASE keyword (Percent adds '%compensation%').
"""

import logging
from typing import Optional, List

import duckdb
import pandas as pd

from tools.salary_extractors.rg_income.extractor import standardize_columns
from tools.salary_extractors.rg_sal.keywords import DEFAULT_PARAMS, METHODS
from tools.salary_extractors.rg_sal.helpers import name_match_ratio, fuzzy_score, ilike_not, ilike_any, merchant_case

logger = logging.getLogger("rg_sal")


class RGSalExtractor:
    """RG_SAL salary extractor (DuckDB). Mirrors RGIncomeExtractor's shape."""

    def __init__(self, **overrides):
        self.params = DEFAULT_PARAMS.copy()
        self.params.update(overrides)
        self.conn = duckdb.connect()
        self.conn.create_function("name_match_ratio", name_match_ratio, ["VARCHAR", "VARCHAR"], "INTEGER")
        self.conn.create_function("fuzzy_score", fuzzy_score, ["VARCHAR", "VARCHAR"], "INTEGER")
        self._max_month = None  # latest relative month index (= latest_month_flag anchor)

    def close(self):
        self.conn.close()

    # ─── Public entry point ────────────────────────────────────────────────

    def extract(self,
                input_csv: Optional[str] = None,
                df: Optional[pd.DataFrame] = None,
                cust_id_filter: Optional[List[str]] = None,
                start_date: Optional[str] = None,
                end_date: Optional[str] = None) -> pd.DataFrame:
        """Run the full RG_SAL pipeline; returns one salary-summary row per customer."""
        if df is not None: df = df.copy()
        elif input_csv is not None: df = pd.read_csv(input_csv)
        else: raise ValueError("extract() requires either `df` or `input_csv`")

        df = standardize_columns(df)

        # Derive the trailing `lookback_months` window from the data (anchor-from-data).
        if not start_date or not end_date:
            tran_dates = pd.to_datetime(df["TRAN_DATE"], errors="coerce")
            recent = sorted(tran_dates.dt.to_period("M").dropna().unique())[-self.params["lookback_months"]:]
            if not start_date: start_date = recent[0].start_time.strftime("%Y-%m-%d")
            if not end_date: end_date = tran_dates.max().strftime("%Y-%m-%d")
        logger.info(f"[rg_sal] window {start_date} → {end_date}")

        self._base_transform(df, cust_id_filter, start_date, end_date)
        for method in METHODS: self._run_method(method)
        self._hierarchy_merge()
        self._self_transfer_flag()
        return self._summary()

    # ─── Base transform (cells 5-6) ────────────────────────────────────────

    def _base_transform(self, df, cust_ids, start_date, end_date):
        self.conn.register("raw_data", df)
        cust_clause = ""
        if cust_ids:
            ids = ", ".join(f"'{c}'" for c in cust_ids)
            # Cast to VARCHAR so a string filter works regardless of the id column's
            # type (the notebook compares cust_id to string literals).
            cust_clause = f"AND CAST(CUST_ID AS VARCHAR) IN ({ids})"

        # b1_dec: all credit txns in window; relative month index (year-aware, 1..N).
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE b1 AS
            WITH filtered AS (
                SELECT
                    CUST_ID, ACCNT_NUM, PRTY_NAME AS customer_name,
                    CAST(TRAN_DATE AS DATE) AS TRAN_DATE,
                    TRAN_AMT_IN_AC, TRAN_PARTCLR,
                    EXTRACT(YEAR FROM CAST(TRAN_DATE AS DATE)) AS year,
                    EXTRACT(YEAR FROM CAST(TRAN_DATE AS DATE)) * 100
                        + EXTRACT(MONTH FROM CAST(TRAN_DATE AS DATE)) AS yyyymm
                FROM raw_data
                WHERE CURRENT_FLAG = 'Y' AND DEL_FLAG = 'N' AND DR_CR_INDCTOR = 'C'
                  AND CAST(TRAN_DATE AS DATE) BETWEEN '{start_date}' AND '{end_date}'
                  {cust_clause}
            )
            SELECT *, DENSE_RANK() OVER (ORDER BY yyyymm) AS month
            FROM filtered
        """)

        self._max_month = self.conn.execute("SELECT COALESCE(MAX(month), 0) FROM b1").fetchone()[0]

        # monthly_sums: per customer, total credits in each relative month 1..N.
        n = self.params["lookback_months"]
        sum_cols = ",\n                ".join(
            f"SUM(CASE WHEN month = {k} THEN TRAN_AMT_IN_AC ELSE 0 END) AS sum_{k}"
            for k in range(1, n + 1)
        )
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE monthly_sums AS
            SELECT cust_id,
                {sum_cols}
            FROM b1 GROUP BY cust_id
        """)

        # Px = txn ÷ its own relative-month total; date/amount flags (cell 6).
        px_cases = "\n                ".join(
            f"WHEN a.month = {k} THEN a.TRAN_AMT_IN_AC / NULLIF(b.sum_{k}, 0)"
            for k in range(1, n + 1)
        )
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE base_data AS
            SELECT a.*,
                CASE WHEN EXTRACT(DAY FROM a.TRAN_DATE) <= 10
                       OR EXTRACT(DAY FROM a.TRAN_DATE) >= 20 THEN 1 ELSE 0 END AS DATE_BET_20_10,
                CASE
                {px_cases}
                    ELSE 0
                END AS Px,
                CASE WHEN a.TRAN_AMT_IN_AC >= {self.params['min_amount']} THEN 1 ELSE 0 END AS Tran_GTE_10K
            FROM b1 a
            LEFT JOIN monthly_sums b ON a.cust_id = b.cust_id
        """)
        # Px_GTE_40_PER derived separately (references the computed Px column).
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE base_data AS
            SELECT *, CASE WHEN Px >= {self.params['px_threshold']} THEN 1 ELSE 0 END AS Px_GTE_40_PER
            FROM base_data
        """)
        cnt = self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT cust_id) FROM base_data").fetchone()
        logger.info(f"[rg_sal] base_data: {cnt[0]} rows | {cnt[1]} customers | max_month={self._max_month}")

    # ─── One sub-method: filter → merchant → gate → cluster → refine ───────

    def _run_method(self, method: str):
        cfg = METHODS[method]
        p = self.params

        # 1) method exclusion list + pre-gate (+ company whitelist).
        where = f"{cfg['pre_gate']}\n              AND {ilike_not(cfg['exclusions'])}"
        if cfg["whitelist"]:
            where += f"\n              AND {ilike_any(cfg['whitelist'])}"
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE m_data AS
            SELECT * FROM base_data WHERE {where}
        """)

        # 2) merchant extraction + cleaning (cells 9-10). 'g' flag = replace-all (DuckDB).
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE m_raw AS
            SELECT *, {merchant_case(cfg['sal_keywords'])} FROM m_data
        """)
        self.conn.execute("""
            CREATE OR REPLACE TABLE m_clean AS
            SELECT A.*,
                TRIM(REGEXP_REPLACE(LOWER(A.cleaned_string),
                    '(neft|imps|rtgs|ift|ft|upi|chq|cash|transfer|trf|mb:|payment|paymen)',
                    ' ', 'g')) AS merchant
            FROM (
                SELECT *, TRIM(REGEXP_REPLACE(remitter, '[^a-zA-Z\\s]', ' ', 'g')) AS cleaned_string
                FROM m_raw
            ) AS A
        """)

        # 3) per-customer gate: 3..100 credit txns AND >=3 distinct months (cells 10/16/23).
        #    Salary method additionally keeps only merchant='salary' rows first (cell 16).
        salary_filter = "WHERE merchant = 'salary'" if cfg["salary_only"] else ""
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE m_num AS
            SELECT *, ROW_NUMBER() OVER (ORDER BY cust_id, tran_partclr, tran_date, tran_amt_in_ac) AS row_id
            FROM m_clean {salary_filter}
        """)
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE m_gate1 AS
            SELECT A.*
            FROM m_num A
            JOIN (SELECT cust_id, COUNT(*) AS n FROM m_num GROUP BY cust_id) B USING (cust_id)
            WHERE B.n >= {p['min_txn']} AND B.n <= {p['max_txn']}
        """)
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE m_gate2 AS
            SELECT A.*
            FROM m_gate1 A
            JOIN (SELECT cust_id, COUNT(DISTINCT month) AS nm FROM m_gate1 GROUP BY cust_id) B USING (cust_id)
            WHERE B.nm >= {p['min_months']}
        """)

        # 4) merchant clustering (cells 11-12). DIFFERENCE>3 → name_match_ratio>=70.
        self.conn.execute("""
            CREATE OR REPLACE TABLE m_sim AS
            SELECT DISTINCT cust_id, accnt_num, tran_date, tran_amt_in_ac, tran_partclr, merchant, year, month,
                DENSE_RANK() OVER (PARTITION BY cust_id ORDER BY merchant, tran_date, tran_amt_in_ac, row_id) AS id
            FROM m_gate2
        """)
        self.conn.execute("""
            CREATE OR REPLACE TABLE m_pairs AS
            SELECT A.cust_id, A.merchant AS S1, A.id AS ID_S1, B.merchant AS S2, B.id AS ID_S2
            FROM m_sim A JOIN m_sim B ON A.cust_id = B.cust_id AND A.id < B.id
        """)
        thr = p["cluster_fuzzy"]
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE m_fuzzy AS
            SELECT S1, S2, score FROM (
                SELECT DISTINCT S1, S2, name_match_ratio(S1, S2) AS score FROM m_pairs
            ) WHERE score >= {thr}
        """)
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE m_sig AS
            SELECT p.cust_id, p.S1, p.ID_S1, p.S2, p.ID_S2, f.score
            FROM m_pairs p JOIN m_fuzzy f ON p.S1 = f.S1 AND p.S2 = f.S2
            WHERE f.score >= {thr}
        """)
        self.conn.execute("""
            CREATE OR REPLACE TABLE m_cluster AS
            WITH m7 AS (
                SELECT *, RANK() OVER (PARTITION BY cust_id ORDER BY ID_S1) AS cluster_id FROM m_sig
            ),
            m8 AS (
                SELECT DISTINCT cust_id, ID_S2 AS unique_id, cluster_id FROM m7
                UNION
                SELECT DISTINCT cust_id, ID_S1 AS unique_id, cluster_id FROM m7
            ),
            m9 AS (
                SELECT cust_id, unique_id, cluster_id FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY cust_id, unique_id ORDER BY cluster_id ASC) AS rnk FROM m8
                ) WHERE rnk = 1
            )
            SELECT A.*, B.cluster_id
            FROM m_sim A LEFT JOIN m9 B ON A.cust_id = B.cust_id AND A.id = B.unique_id
        """)
        # *_test1 equivalent (cell 13/19/25).
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE {method}_test1 AS
            SELECT cust_id, accnt_num, cluster_id, tran_date, tran_amt_in_ac, merchant, tran_partclr, month, year
            FROM m_cluster
        """)

        # 5) refinement (cells 29-31) → *_test2.
        self._refine(method)
        cnt = self.conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT cust_id) FROM {method}_test2").fetchone()
        logger.info(f"[rg_sal] method={method}: {cnt[0]} rows | {cnt[1]} customers")

    # ─── Refinement (cells 29-31 / 33 / 34-35) ─────────────────────────────

    def _refine(self, method: str):
        p = self.params
        # step1: max txn per (cust,cluster,month); keep clusters with >=3 months & 3..7 rows.
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE r_step1 AS
            WITH base AS (
                SELECT * FROM {method}_test1 WHERE cluster_id IS NOT NULL
            ),
            ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY cust_id, cluster_id, month
                    ORDER BY tran_amt_in_ac DESC, tran_date ASC) AS rn
                FROM base
            ),
            max_per_month AS (SELECT * FROM ranked WHERE rn = 1),
            valid AS (
                SELECT cust_id, cluster_id FROM max_per_month
                GROUP BY cust_id, cluster_id
                HAVING COUNT(DISTINCT month) >= {p['min_months']}
                   AND COUNT(*) BETWEEN {p['refine_min_txn']} AND {p['refine_max_txn']}
            )
            SELECT m.* FROM max_per_month m JOIN valid v USING (cust_id, cluster_id)
        """)

        # step2: day-of-month proximity flag + ±20%/±50% median bands.
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE r_step2 AS
            WITH date_flags AS (
                SELECT vt.*,
                    EXTRACT(DAY FROM tran_date) AS trans_day,
                    EXTRACT(DAY FROM FIRST_VALUE(tran_date) OVER (
                        PARTITION BY cust_id ORDER BY tran_date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)) AS base_day,
                    CASE WHEN LEAST(
                        ABS(EXTRACT(DAY FROM tran_date) - EXTRACT(DAY FROM FIRST_VALUE(tran_date) OVER (
                            PARTITION BY cust_id ORDER BY tran_date
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING))),
                        31 - ABS(EXTRACT(DAY FROM tran_date) - EXTRACT(DAY FROM FIRST_VALUE(tran_date) OVER (
                            PARTITION BY cust_id ORDER BY tran_date
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)))
                    ) <= {p['day_diff']} THEN 1 ELSE 0 END AS day_diff_in_5
                FROM r_step1 vt
            ),
            med AS (
                SELECT cust_id, cluster_id,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tran_amt_in_ac) AS median_amt
                FROM date_flags GROUP BY cust_id, cluster_id
            )
            SELECT df.*, m.median_amt,
                   CASE WHEN ABS(tran_amt_in_ac - m.median_amt) <= {p['median_tol']} * m.median_amt THEN 1 ELSE 0 END AS range_status,
                   CASE WHEN ABS(tran_amt_in_ac - m.median_amt) <= {p['median_tol_50']} * m.median_amt THEN 1 ELSE 0 END AS range_status_50_per
            FROM date_flags df JOIN med m USING (cust_id, cluster_id)
        """)

        # step3: consecutive-month flag, last-3 median/min, final_salary_amt + identifier.
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE {method}_test2 AS
            WITH amount_summary AS (
                SELECT cust_id, cluster_id,
                       SUM(range_status) AS range_status_1,
                       COUNT(*) AS total_cnt,
                       SUM(range_status_50_per) AS range_status_50_per1
                FROM r_step2 GROUP BY cust_id, cluster_id
            ),
            month_tagged AS (
                SELECT *, CHR(CAST(64 + month AS INTEGER)) AS month_tag FROM r_step2
            ),
            ranked_months AS (
                SELECT cust_id, cluster_id, month_tag, tran_date,
                       ROW_NUMBER() OVER (PARTITION BY cust_id, cluster_id ORDER BY tran_date) AS rn
                FROM month_tagged
            ),
            consecutive_flag AS (
                SELECT a.cust_id, a.cluster_id,
                       MAX(CASE WHEN ((ASCII(a.month_tag) - 65 + 1) % 26) = (ASCII(b.month_tag) - 65)
                                 AND ((ASCII(b.month_tag) - 65 + 1) % 26) = (ASCII(c.month_tag) - 65)
                                THEN 1 ELSE 0 END) AS consecutive
                FROM ranked_months a
                JOIN ranked_months b ON a.cust_id = b.cust_id AND a.cluster_id = b.cluster_id AND b.rn = a.rn + 1
                JOIN ranked_months c ON a.cust_id = c.cust_id AND a.cluster_id = c.cluster_id AND c.rn = b.rn + 1
                GROUP BY a.cust_id, a.cluster_id
            ),
            last3_stats AS (
                SELECT cust_id, cluster_id,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tran_amt_in_ac) AS median_amt,
                       MIN(tran_amt_in_ac) AS min_amt
                FROM (
                    SELECT cust_id, cluster_id, tran_amt_in_ac,
                           ROW_NUMBER() OVER (PARTITION BY cust_id, cluster_id ORDER BY tran_date DESC) AS rn
                    FROM r_step2
                ) WHERE rn <= 3
                GROUP BY cust_id, cluster_id
            ),
            final_salary AS (
                SELECT s.cust_id, s.cluster_id,
                    CASE WHEN a.range_status_1 = a.total_cnt THEN s.median_amt ELSE s.min_amt END AS final_salary_amt,
                    CASE WHEN a.range_status_1 = a.total_cnt THEN 'MEDIAN' ELSE 'MIN' END AS identifier,
                    cf.consecutive, a.range_status_50_per1
                FROM last3_stats s
                JOIN amount_summary a USING (cust_id, cluster_id)
                LEFT JOIN consecutive_flag cf USING (cust_id, cluster_id)
            )
            SELECT f.cust_id, f.cluster_id, f.final_salary_amt, f.identifier, f.consecutive,
                   f.range_status_50_per1,
                   t.accnt_num, t.month, t.tran_date, t.merchant, t.tran_partclr,
                   t.tran_amt_in_ac, t.day_diff_in_5, t.range_status, t.range_status_50_per
            FROM final_salary f
            JOIN r_step2 t USING (cust_id, cluster_id)
        """)

    # ─── Hierarchy merge (cell 38-39) ──────────────────────────────────────

    def _hierarchy_merge(self):
        # Pick one method per customer (Salary>Percent>Company); take ALL its rows.
        # latest_month_flag: row is in the latest relative window month (notebook's `=6`).
        max_month = self._max_month or self.params["lookback_months"]
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE strings_final AS
            WITH method_candidates AS (
                SELECT DISTINCT cust_id, 1 AS priority FROM salary_test2 UNION ALL
                SELECT DISTINCT cust_id, 2 AS priority FROM percent_test2 UNION ALL
                SELECT DISTINCT cust_id, 3 AS priority FROM company_test2
            ),
            chosen AS (SELECT cust_id, MIN(priority) AS pri FROM method_candidates GROUP BY cust_id),
            salary_rows AS (
                SELECT s.*, 'Salary' AS chosen_method FROM salary_test2 s
                JOIN chosen c ON c.cust_id = s.cust_id AND c.pri = 1
            ),
            percent_rows AS (
                SELECT p.*, 'Percent' AS chosen_method FROM percent_test2 p
                JOIN chosen c ON c.cust_id = p.cust_id AND c.pri = 2
            ),
            company_rows AS (
                SELECT co.*, 'Company' AS chosen_method FROM company_test2 co
                JOIN chosen c ON c.cust_id = co.cust_id AND c.pri = 3
            ),
            unioned AS (
                SELECT * FROM salary_rows UNION ALL
                SELECT * FROM percent_rows UNION ALL
                SELECT * FROM company_rows
            )
            SELECT f.*,
                   CASE WHEN f.month = {max_month} THEN 1 ELSE 0 END AS latest_month_flag,
                   CASE WHEN f.tran_partclr LIKE '%PENSION%' THEN 1 ELSE 0 END AS pension_pay_flag
            FROM unioned f
        """)

    # ─── Self-transfer flag (cells 40-44) ──────────────────────────────────

    def _self_transfer_flag(self):
        # customer_name from PRTY_NAME on credits >=10000 (cell 40); one per customer.
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE cust_name AS
            SELECT cust_id, customer_name FROM (
                SELECT cust_id, customer_name,
                       ROW_NUMBER() OVER (PARTITION BY cust_id ORDER BY customer_name) AS rn
                FROM (SELECT DISTINCT cust_id, customer_name FROM base_data
                      WHERE TRAN_AMT_IN_AC >= {self.params['min_amount']})
            ) WHERE rn = 1
        """)
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE strings_scored AS
            WITH j AS (
                SELECT a.*, LOWER(b.customer_name) AS cust_name,
                       LOWER(a.tran_partclr) AS trxn, LOWER(a.merchant) AS l_merchant
                FROM strings_final a
                LEFT JOIN cust_name b ON a.cust_id = b.cust_id
            ),
            scored AS (
                SELECT *,
                    CASE WHEN cust_name IS NULL OR trxn IS NULL THEN 0
                         ELSE fuzzy_score(cust_name, l_merchant) END AS score
                FROM j
            )
            SELECT *,
                CASE WHEN (score > {self.params['self_transfer_fuzzy']}
                          AND l_merchant NOT ILIKE '%salary%') THEN 1 ELSE 0 END AS flag
            FROM scored
        """)

    def get_salary_strings(self) -> pd.DataFrame:
        """Transaction-level 'strings' behind the chosen method per customer.

        The rows that feed the summary — date, narration, amount, method, and the
        self-transfer / pension flags. Call after extract(). (salary_strings_final1
        equivalent, cell 42.)
        """
        return self.conn.execute("""
            SELECT cust_id, accnt_num, chosen_method AS method, cluster_id,
                   month, tran_date, merchant, tran_partclr, tran_amt_in_ac,
                   final_salary_amt, identifier, consecutive, day_diff_in_5,
                   latest_month_flag, pension_pay_flag, score, flag AS self_transfer
            FROM strings_scored
            ORDER BY cust_id, month, tran_date
        """).df()

    # ─── Summary (cell 46) ─────────────────────────────────────────────────

    def _summary(self) -> pd.DataFrame:
        month_pivots = ",\n                ".join(
            f"MAX(CASE WHEN month = {k} THEN tran_amt_in_ac END) AS month_{k}_amt"
            for k in range(1, 13)
        )
        return self.conn.execute(f"""
            WITH s AS (
                SELECT
                    cust_id AS crn,
                    MAX(final_salary_amt) AS final_salary,
                    MAX(identifier) AS identifier,
                    MAX(consecutive) AS consecutive,
                    MIN(day_diff_in_5) AS day_diff_in_5,
                    MAX(chosen_method) AS method,
                    MAX(latest_month_flag) AS latest_month_flag,
                    MAX(pension_pay_flag) AS pension_pay_flag,
                    MAX(flag) AS self_transfer,
                    {month_pivots}
                FROM strings_scored
                GROUP BY cust_id
            )
            SELECT *,
                CASE
                    WHEN latest_month_flag > 0 AND consecutive > 0 AND day_diff_in_5 > 0 THEN 'LATEM_CONSE_WT5'
                    WHEN latest_month_flag > 0 AND consecutive = 0 AND day_diff_in_5 > 0 THEN 'LATEM_NCONSE_WT5'
                    WHEN latest_month_flag > 0 AND consecutive > 0 AND day_diff_in_5 = 0 THEN 'LATEM_CONSE_NWT5'
                    WHEN latest_month_flag > 0 AND consecutive = 0 AND day_diff_in_5 = 0 THEN 'LATEM_NCONSE_NWT5'
                    WHEN latest_month_flag = 0 AND consecutive > 0 AND day_diff_in_5 > 0 THEN 'NLATEM_CONSE_WT5'
                    WHEN latest_month_flag = 0 AND consecutive = 0 AND day_diff_in_5 > 0 THEN 'NLATEM_NCONSE_WT5'
                    WHEN latest_month_flag = 0 AND consecutive > 0 AND day_diff_in_5 = 0 THEN 'NLATEM_CONSE_NWT5'
                    WHEN latest_month_flag = 0 AND consecutive = 0 AND day_diff_in_5 = 0 THEN 'NLATEM_NCONSE_NWT5'
                    ELSE 'NA'
                END AS Income_Type_Flag
            FROM s
            ORDER BY crn
        """).df()


# ─── Module-level facade (mirrors _calculate_credit_based_income) ───────────

def rg_sal_calculate(df: Optional[pd.DataFrame] = None,
                     input_csv: Optional[str] = None,
                     cust_id_filter: Optional[List[str]] = None,
                     **overrides) -> pd.DataFrame:
    """Compute RG_SAL salary — one summary row per customer.

    Pass a standardized/raw transactions frame via `df` or a CSV path via
    `input_csv`; optionally restrict to `cust_id_filter`. Returns the cell-46
    salary_summary_final: crn, final_salary, identifier, consecutive, day_diff_in_5,
    method, latest_month_flag, pension_pay_flag, self_transfer, month_1..12_amt,
    Income_Type_Flag.
    """
    ext = RGSalExtractor(**overrides)
    return ext.extract(df=df, input_csv=input_csv, cust_id_filter=cust_id_filter)
