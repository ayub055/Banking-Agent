"""Consolidate the tab-separated CSVs in ``excel_format/`` into one .xlsx workbook.

Each source CSV becomes one worksheet (tab name = filename with the ``sheetN[_M]_``
prefix stripped). Multi-block / pivot layouts are normalised into clean tabular
data, and the raw formatting quirks are fixed:
  * files are tab-delimited (values use Indian comma grouping, e.g. ``5,32,708.63``)
  * negatives are parenthesised, e.g. ``(1,786.00)`` -> ``-1786.0``
  * amounts carry trailing spaces

Run:  python build_excel_format.py
Out:  excel_format/bank_statement_analysis.xlsx
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from tools.excel_utils import clean_number, read_rows, cell, tab_name, write_sheet

SRC_DIR = Path(__file__).resolve().parent / "excel_format"
OUT_PATH = SRC_DIR / "bank_statement_analysis.xlsx"

MONTH_TOKEN = re.compile(r"^[A-Za-z]{3}[/-]\d{2}$")          # Feb/26, May-26
MONTH_HEADING = re.compile(r"^[A-Za-z]{3}-\d{2}$")            # Feb-26 (block heading)

LEDGER_COLS = ["Sl.No", "Date", "Cheque No", "Description", "Amount", "Category", "Balance"]


# --------------------------------------------------------------------------- #
# per-sheet parsers  -> return either a DataFrame or a list of (title, DataFrame)
# --------------------------------------------------------------------------- #
def parse_metric_region(rows, label_col, value_cols):
    """Unpivot a Summary/Bouncing/Balances block into (Section, Metric, *values) rows.

    A row is a *section header* when all of its non-empty value cells are month tokens.
    """
    records, months, section = [], None, None
    for r in rows:
        label = cell(r, label_col)
        vals = [cell(r, c) for c in value_cols]
        if not label and not any(vals):
            continue
        non_empty = [v for v in vals if v]
        if non_empty and all(MONTH_TOKEN.match(v) for v in non_empty):
            section, months = label, [v.replace("/", "-") for v in vals]
            continue
        if label and non_empty:
            records.append([section, label] + [clean_number(v) for v in vals])
    return records, months


def parse_analysis(rows):
    """sheet1: Account Info key/value table + Monthly Metrics table."""
    # --- account info: rows between 'Summary Info' and the first blank row ---
    start = next(i for i, r in enumerate(rows) if cell(r, 0) == "Summary Info") + 1
    info = []
    for r in rows[start:]:
        key, value = cell(r, 1), cell(r, 2)
        if not key and not value:
            break
        info.append([key, value])
    info_df = pd.DataFrame(info, columns=["Field", "Value"])

    left, months = parse_metric_region(rows, label_col=1, value_cols=[2, 3, 4])
    right, _ = parse_metric_region(rows, label_col=6, value_cols=[7, 8, 9])
    cols = ["Section", "Metric"] + (months or ["Feb-26", "Mar-26", "Apr-26"])
    metrics_df = pd.DataFrame(left + right, columns=cols)
    return [("Account Info", info_df), ("Monthly Metrics", metrics_df)]


def parse_month_wise(rows):
    """sheet9: same idea as Analysis but a single month, no leading spacer column."""
    left, months = parse_metric_region(rows, label_col=0, value_cols=[1])
    right, _ = parse_metric_region(rows, label_col=3, value_cols=[4])
    cols = ["Section", "Metric"] + (months or ["May-26"])
    return pd.DataFrame(left + right, columns=cols)


def parse_loan_track(rows):
    """sheet2_x: unpivot the category x month pivot into Category | Amount | Month | Dates."""
    header = rows[1]                                   # row0 = 'LOAN TRACK'
    amounts = rows[2]
    categories = list(range(1, len(header)))           # column indices with a category header
    records = []
    for r in rows[3:]:                                 # each month row
        month = cell(r, 0)
        if not month:
            continue
        for col in categories:
            cat = cell(header, col)
            dates = cell(r, col)
            if cat and dates:
                records.append([cat, clean_number(cell(amounts, col)), month.replace("/", "-"), dates])
    return pd.DataFrame(records, columns=["Category", "Amount", "Month", "Dates"])


def parse_funds(rows):
    """sheet5/sheet6: per-month Description/Amount blocks -> Month | Description | Amount."""
    records, month = [], None
    for r in rows:
        c0, c1 = cell(r, 0), cell(r, 1)
        if c0 and not c1 and MONTH_HEADING.match(c0):
            month = c0
            continue
        if c0 == "Description" or (not c0 and not c1):
            continue
        records.append([month, c0, clean_number(c1)])
    return pd.DataFrame(records, columns=["Month", "Description", "Amount"])


def parse_eod(rows):
    """sheet3: Day | Feb-26 | Mar-26 | Apr-26 (already tabular, just clean numbers)."""
    header = [c.replace("/", "-") for c in rows[0]]
    data = [[clean_number(c) if i else cell(r, 0) for i, c in enumerate(r)] for r in rows[1:]]
    return pd.DataFrame(data, columns=header)


def parse_ledger(rows, has_group: bool):
    """sheet4/sheet7/sheet8_credits: clean ledger; numeric Amount & Balance."""
    cols = (["Group"] + LEDGER_COLS) if has_group else LEDGER_COLS
    amt_idx = cols.index("Amount")
    bal_idx = cols.index("Balance")
    data = []
    for r in rows[1:]:
        row = [cell(r, i) for i in range(len(cols))]
        row[amt_idx] = clean_number(row[amt_idx])
        row[bal_idx] = clean_number(row[bal_idx])
        data.append(row)
    return pd.DataFrame(data, columns=cols)


def parse_fcu(rows):
    """sheet10: split multi-line indicator cells; tag each with its group."""
    records, group = [], None
    for r in rows:
        num, body = cell(r, 0), cell(r, 1)
        if body in ("Possible Fraud Indicators", "Behavioural/Transactional Indicators"):
            group = body
            continue
        if not num.isdigit():                          # skip 'Total ...' summary rows
            continue
        name, _, desc = body.partition("\n")
        records.append([group, int(num), name.strip(), desc.strip(), cell(r, 4), cell(r, 5)])
    return pd.DataFrame(records, columns=["Group", "#", "Indicator", "Description", "Identified", "Count/Remarks"])


def main():
    # explicit order -> stable tab order sheet1..sheet10
    plan = [
        ("sheet1_Analysis.csv",              lambda r: parse_analysis(r)),
        ("sheet2_1_Loan_credit_Recieved.csv", lambda r: parse_loan_track(r)),
        ("sheet2_2_Loan_emi_debit.csv",       lambda r: parse_loan_track(r)),
        ("sheet3_EOD_Balances.csv",          lambda r: parse_eod(r)),
        ("sheet4_xns.csv",                   lambda r: parse_ledger(r, has_group=False)),
        ("sheet5_funds_recieved.csv",        lambda r: parse_funds(r)),
        ("sheet6_funds_remitences.csv",      lambda r: parse_funds(r)),
        ("sheet7_salary_txns.csv",           lambda r: parse_ledger(r, has_group=False)),
        ("sheet8_recurring_credits.csv",     lambda r: parse_ledger(r, has_group=True)),
        ("sheet8_recurring_debits.csv",      None),  # placeholder -> rebuilt from sheet7 schema
        ("sheet9_month_wise_analysis.csv",   lambda r: parse_month_wise(r)),
        ("sheet10_fcu_idicators.csv",        lambda r: parse_fcu(r)),
    ]

    with pd.ExcelWriter(OUT_PATH, engine="openpyxl") as writer:
        for filename, parser in plan:
            sheet = tab_name(filename)
            if parser is None:
                # sheet8_recurring_debits: rebuild sheet7 headers, no data rows
                payload = pd.DataFrame(columns=["Group"] + LEDGER_COLS)
            else:
                payload = parser(read_rows(SRC_DIR / filename))
            write_sheet(writer, sheet, payload)
            print(f"  {filename:38s} -> tab '{sheet}'")

    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
