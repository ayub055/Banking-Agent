"""
RG_SAL helper functions.

Two Python UDFs that stand in for missing DuckDB builtins (registered on the
connection by RGSalExtractor), plus the small SQL-fragment builders that turn the
keyword lists in keywords.py into SQL. No DuckDB/state here — pure functions.
"""

from typing import List

from fuzzywuzzy import fuzz


# ─── Fuzzy UDFs (stand in for missing DuckDB builtins) ──────────────────────

def name_match_ratio(a, b) -> int:
    """Replaces Snowflake/Redshift DIFFERENCE() for merchant↔merchant clustering.

    DIFFERENCE returns a 0–4 Soundex overlap; `> 3` means a near-exact phonetic
    match. fuzzywuzzy `partial_ratio` (0–100) is the §3 convention; the `> 3`
    threshold maps to `>= 70`.
    """
    if a is None or b is None:
        return 0
    na = "".join(ch for ch in str(a).lower() if ch.isalnum())
    nb = "".join(ch for ch in str(b).lower() if ch.isalnum())
    if not na or not nb:
        return 0
    return int(fuzz.partial_ratio(na, nb))


def fuzzy_score(a, b) -> int:
    """Replaces the notebook's `fuzzy_score(cust_name, merchant)` self-transfer check.

    A 0–100 similarity between the account-holder's own name and the extracted
    remitter/merchant; the pipeline keeps `> 60`. Uses `fuzz.ratio` (whole-string
    similarity) since both operands are full names here.
    """
    if a is None or b is None:
        return 0
    return int(fuzz.ratio(str(a).lower(), str(b).lower()))


# ─── SQL-fragment builders ──────────────────────────────────────────────────

def ilike_not(patterns: List[str]) -> str:
    return "\n              AND ".join(f"TRAN_PARTCLR NOT ILIKE '{p}'" for p in patterns)


def ilike_any(patterns: List[str]) -> str:
    return "(" + " OR ".join(f"TRAN_PARTCLR ILIKE '{p}'" for p in patterns) + ")"


def merchant_case(sal_keywords: List[str]) -> str:
    """The remitter/merchant extraction CASE (cells 9/15/22), transcribed verbatim.

    Only the salary-keyword WHEN block varies across methods; everything else is
    identical. `\\s` regex escapes are DuckDB-compatible; the prefix REGEXP_REPLACE
    strips mirror rg_income (first-match, single prefix per narration).
    """
    sal_clause = " OR\n            ".join(f"tran_partclr ILIKE '{p}'" for p in sal_keywords)
    return f"""
        CASE
            WHEN
            {sal_clause}
            THEN 'SALARY'
            WHEN tran_partclr ILIKE '%IFT%' THEN
                CASE
                    WHEN tran_partclr ILIKE '%IFT-%' THEN SPLIT_PART(tran_partclr, '-', 2)
                    ELSE TRIM(REGEXP_REPLACE(tran_partclr, 'IFT\\s*', ''))
                END
            WHEN tran_partclr ILIKE 'FROM %' THEN
                TRIM(
                    SPLIT_PART(tran_partclr, ' ', 2) ||
                    COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 3), '') ||
                    COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 4), '')
                )
            WHEN tran_partclr ILIKE '%NACH%' THEN
                CASE
                     WHEN tran_partclr ILIKE '%NACH-%' THEN SPLIT_PART(tran_partclr, '-', 4)
                     ELSE TRIM(REGEXP_REPLACE(tran_partclr, 'NACH\\s*', ''))
                END
            WHEN tran_partclr ILIKE 'IB:RECEIVED FROM%' THEN TRIM(SPLIT_PART(tran_partclr, 'FROM', 2))
            WHEN tran_partclr ILIKE 'FUND TRF FROM%' THEN TRIM(SPLIT_PART(tran_partclr, 'FROM', 2))
            WHEN tran_partclr ILIKE 'FT FROM%' THEN TRIM(SPLIT_PART(tran_partclr, 'FROM', 2))
            WHEN tran_partclr ILIKE 'FUNDS TRF FROM%' THEN TRIM(SPLIT_PART(tran_partclr, 'FROM', 2))
            WHEN tran_partclr ILIKE '%UPI%' THEN
                CASE
                     WHEN tran_partclr ILIKE '%UPI/%' THEN SPLIT_PART(tran_partclr, '/', 2)
                     ELSE TRIM(REGEXP_REPLACE(tran_partclr, 'UPI\\s*', ''))
                END
            WHEN tran_partclr ILIKE '%IMPS%' THEN
                CASE
                    WHEN tran_partclr ILIKE '%IMPS/%' THEN SPLIT_PART(tran_partclr, '/', 3)
                    ELSE TRIM(REGEXP_REPLACE(tran_partclr, 'IMPS\\s*', ''))
                END
            WHEN tran_partclr ILIKE '%MB:RECEIVED FROM%' THEN TRIM(SPLIT_PART(tran_partclr, 'FROM', 2))
            WHEN tran_partclr ILIKE '%IB:FUND%' THEN TRIM(SPLIT_PART(tran_partclr, 'FROM', 2))
            WHEN tran_partclr ILIKE '%NEFT%' THEN
                CASE
                    WHEN tran_partclr ILIKE '%NEFT %' THEN TRIM(SPLIT_PART(tran_partclr, ' ', 3) ||
                        COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 4), '') ||
                        COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 5), '') ||
                        COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 6), ''))
                    ELSE TRIM(REGEXP_REPLACE(tran_partclr, 'NEFT\\s*', ''))
                END
            WHEN tran_partclr ILIKE '%IB: FUND TRANSFER%' THEN TRIM(SPLIT_PART(tran_partclr, ' ', 5) ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 4), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 6), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 7), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 8), ''))
            WHEN tran_partclr ILIKE '%FUND TRANSFER FROM%' THEN TRIM(SPLIT_PART(tran_partclr, ' ', 5) ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 4), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 6), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 7), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 8), ''))
            WHEN tran_partclr ILIKE '%FUNDS TRANSFER FROM%' THEN TRIM(SPLIT_PART(tran_partclr, ' ', 4) ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 4), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 5), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 6), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 7), ''))
            WHEN tran_partclr ILIKE '%FT FROM%' THEN TRIM(SPLIT_PART(tran_partclr, ' ', 3) ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 4), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 5), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 6), '') ||
                COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 7), ''))
            WHEN tran_partclr ILIKE '%RTGS%' THEN
                CASE
                    WHEN tran_partclr ILIKE '%RTGS %' THEN TRIM(SPLIT_PART(tran_partclr, ' ', 3) ||
                        COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 4), '') ||
                        COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 5), '') ||
                        COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 6), '') ||
                        COALESCE(' ' || SPLIT_PART(tran_partclr, ' ', 7), ''))
                    ELSE TRIM(REGEXP_REPLACE(tran_partclr, 'RTGS\\s*', ''))
                END
            ELSE TRIM(REGEXP_REPLACE(tran_partclr, '(NEFT|IFT|RTGS|UPI|IMPS|MB):?\\s*', ''))
        END AS remitter
    """
