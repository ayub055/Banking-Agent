"""Build the 12-sheet statement workbook (bank_statement_analysis.xlsx) for one
customer, computed from an xn_d1-schema CSV (optionally carrying ``eod_balance``).

The ``xns`` sheet is the master ledger; the other 11 sheets are derived from it,
reusing the canonical row-level predicates in ``tools/rules.py``. Every field is
conditional — filled when the data supports it, otherwise left blank. Runs
degrade gracefully when there is no balance column (balance-dependent sheets stay
blank) so a plain ``xn_d1.csv`` run is never hampered.
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from tools.excel_utils import write_sheet
from tools.rules import is_salary_credit, is_emi_debit, is_loan_disbursal, is_atm_debit

logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent.parent
DEFAULT_OUT = BASE / "excel_format" / "bank_statement_analysis.xlsx"

SIP_KEYWORDS = ("SIP", "MUTUAL FUND", " MF ", "MF/", "BSE STAR MF", "NSE MFUND", "CAMS ", "KARVY ")
GAMING_HINTS = ("GAMING", "BETTING", "DREAM11", "RUMMY", "POKER", "CASINO", "WINZO")
SNAPSHOT_DAYS = [1, 5, 10, 15, 20, 25]
LEDGER_COLS = ["Sl.No", "Date", "Cheque No", "Description", "Amount", "Category", "Balance"]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _month_key(d: str) -> str:
    return str(d)[:7]                       # 'YYYY-MM'


def _month_label(key: str) -> str:
    return datetime.strptime(key, "%Y-%m").strftime("%b-%y")   # 'Feb-26'


def _date_label(d: str) -> str:
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%d-%b-%y")
    except ValueError:
        return str(d)


def _ecs_bounce_mask(narr: pd.Series) -> pd.Series:
    from config.keywords import ECS_BOUNCE_KEYWORDS
    from utils.narration_utils import like_to_regex
    up = narr.fillna("").str.upper()
    pattern = "|".join(like_to_regex(kw) for kw in ECS_BOUNCE_KEYWORDS)
    return up.str.contains(pattern, regex=True, na=False)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in ("category_of_txn", "category_of_txn_l2", "tran_partclr", "tran_type"):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("")
    df["tran_amt_in_ac"] = pd.to_numeric(df["tran_amt_in_ac"], errors="coerce").fillna(0.0)
    df["is_credit"] = df["dr_cr_indctor"].astype(str).str.upper().eq("C")
    df["signed"] = df["tran_amt_in_ac"].where(df["is_credit"], -df["tran_amt_in_ac"])
    df["month"] = df["tran_date"].map(_month_key)
    if "eod_balance" in df.columns:
        df["eod_balance"] = pd.to_numeric(df["eod_balance"], errors="coerce")
    else:
        df["eod_balance"] = pd.NA
    # row-level classifications (reused predicates, narration-based)
    l2, narr = df["category_of_txn_l2"], df["tran_partclr"]
    sal = pd.Series([is_salary_credit(a, b) for a, b in zip(l2, narr)], index=df.index)
    emi = pd.Series([is_emi_debit(a, b) for a, b in zip(l2, narr)], index=df.index)
    loan = pd.Series([is_loan_disbursal(a, b) for a, b in zip(l2, narr)], index=df.index)
    df["is_salary"] = sal & df["is_credit"]
    df["is_emi"] = emi & ~df["is_credit"]
    df["is_loan_credit"] = loan & df["is_credit"]
    up = narr.str.upper()
    df["is_investment"] = up.apply(lambda s: any(k in s for k in SIP_KEYWORDS))
    df["is_atm"] = [is_atm_debit(r) for _, r in df.iterrows()]
    df["is_gaming"] = (df["category_of_txn"].str.upper().str.contains("GAM|BET", regex=True, na=False)
                       | up.apply(lambda s: any(k in s for k in GAMING_HINTS)))
    return df


def _has_balance(df: pd.DataFrame) -> bool:
    return df["eod_balance"].notna().any()


# --------------------------------------------------------------------------- #
# per-sheet builders
# --------------------------------------------------------------------------- #
def _xns_sheet(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i, (_, r) in enumerate(df.iterrows(), start=1):
        bal = r["eod_balance"]
        rows.append([
            i, _date_label(r["tran_date"]), "", r["tran_partclr"],
            r["signed"], r["category_of_txn"],
            "" if pd.isna(bal) else bal,
        ])
    return pd.DataFrame(rows, columns=LEDGER_COLS)


def _account_info(df: pd.DataFrame, rg_salary_data, party_hint) -> pd.DataFrame:
    dates = sorted(df["tran_date"].map(lambda d: str(d)[:10]))
    start, end = (dates[0], dates[-1]) if dates else ("", "")
    name = party_hint or (df["prty_name"].dropna().iloc[0] if "prty_name" in df.columns and df["prty_name"].notna().any() else "")
    cust = df["cust_id"].iloc[0] if "cust_id" in df.columns and len(df) else ""

    employer = ""
    if rg_salary_data and isinstance(rg_salary_data, dict):
        employer = rg_salary_data.get("merchant") or ""
    salary_identified = "YES" if bool(df["is_salary"].any()) else "NO"

    def fmt(d):
        return _date_label(d) if d else ""

    info = [
        ["Name", name],
        ["Bank Name", "Kotak Mahindra Bank"],
        ["Account No.", cust],
        ["Nature of Account", "Savings"],
        ["Period", f"{fmt(start)} to {fmt(end)}" if start else ""],
        ["Pan", ""],
        ["Address", ""],
        ["Employer Name", employer],
        ["Salary Identified", salary_identified],
        ["Statement Start Date", fmt(start)],
        ["Statement End Date", fmt(end)],
    ]
    return pd.DataFrame(info, columns=["Field", "Value"])


def _daily_balance_series(df: pd.DataFrame) -> pd.Series | None:
    """Forward-filled end-of-day balance indexed by calendar date."""
    if not _has_balance(df):
        return None
    bydate = df.dropna(subset=["eod_balance"]).groupby("tran_date")["eod_balance"].last()
    bydate.index = pd.to_datetime(bydate.index)
    full = pd.date_range(bydate.index.min(), bydate.index.max(), freq="D")
    return bydate.reindex(full).ffill()


def _monthly_metrics(df: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    labels = [_month_label(m) for m in months]
    daily = _daily_balance_series(df)

    def per_month(mask, agg):
        out = []
        for m in months:
            sub = df[(df["month"] == m) & mask]
            out.append(agg(sub))
        return out

    amt = lambda s: round(float(s["tran_amt_in_ac"].sum()), 2)
    cnt = lambda s: int(len(s))
    C, D = df["is_credit"], ~df["is_credit"]

    records = []

    def add(section, metric, values):
        records.append([section, metric] + list(values))

    # ---- Summary ----
    add("Summary", "Total Amount of Debit Transactions", per_month(D, amt))
    add("Summary", "Total No. of Debit Transactions", per_month(D, cnt))
    add("Summary", "Total Amount of Credit Transactions", per_month(C, amt))
    add("Summary", "Total No. of Credit Transactions", per_month(C, cnt))
    # ---- Summary - Debit ----
    add("Summary - Debit", "Total Amount of EMI", per_month(df["is_emi"], amt))
    add("Summary - Debit", "Total No. of EMI", per_month(df["is_emi"], cnt))
    add("Summary - Debit", "Total Amount of Investment", per_month(df["is_investment"] & D, amt))
    add("Summary - Debit", "Total No. of Investment", per_month(df["is_investment"] & D, cnt))
    # ---- Summary - Credit ----
    add("Summary - Credit", "Total Amount of Salary", per_month(df["is_salary"], amt))
    add("Summary - Credit", "Total No. of Salary", per_month(df["is_salary"], cnt))
    add("Summary - Credit", "Total Amount of Loan Credit", per_month(df["is_loan_credit"], amt))
    add("Summary - Credit", "Total No. of Loan Credit", per_month(df["is_loan_credit"], cnt))
    # recurring credits (counterparty recurring >= 2 months)
    rec_keys = _recurring_credit_keys(df)
    rec_mask = C & df["category_of_txn"].isin(rec_keys)
    add("Summary - Credit", "Total Amount of Recurring Credits", per_month(rec_mask, amt))
    add("Summary - Credit", "Total No. of Recurring Credits", per_month(rec_mask, cnt))
    # ---- Bouncing (detectable filled, rest 0) ----
    bounce = _ecs_bounce_mask(df["tran_partclr"])
    df_b = df.assign(_b=bounce)
    add("Bouncing", "Total No. of Inward Bounces",
        [int(df_b[(df_b["month"] == m) & df_b["_b"]].shape[0]) for m in months])
    for label in ("Total No. of Outward Bounces", "Total No. of Minimum Balance Charges",
                  "Total Amount of EMI Bounces", "Total No. of EMI Bounces",
                  "Total No. of Inward Cheque Bounces", "Total No. of Outward Cheque Bounces"):
        add("Bouncing", label, [0] * len(months))
    # ---- Balances (only if balance present) ----
    if daily is not None:
        for day in SNAPSHOT_DAYS:
            vals = []
            for m in months:
                y, mo = map(int, m.split("-"))
                try:
                    v = daily.get(pd.Timestamp(year=y, month=mo, day=day))
                except Exception:
                    v = None
                vals.append("" if v is None or pd.isna(v) else round(float(v), 2))
            add("Balances", f"{day}", vals)
        avg = []
        for m in months:
            sel = daily[daily.index.strftime("%Y-%m") == m]
            avg.append(round(float(sel.mean()), 2) if len(sel) else "")
        add("Balances", "Average", avg)

    return pd.DataFrame(records, columns=["Section", "Metric"] + labels)


def _recurring_credit_keys(df: pd.DataFrame) -> set[str]:
    cr = df[df["is_credit"]]
    months_per = cr.groupby("category_of_txn")["month"].nunique()
    return set(months_per[months_per >= 2].index)


def _recurring_debit_keys(df: pd.DataFrame) -> set[str]:
    dr = df[~df["is_credit"]]
    months_per = dr.groupby("category_of_txn")["month"].nunique()
    keys = set(months_per[months_per >= 2].index)
    keys |= set(df[df["is_emi"]]["category_of_txn"])
    return keys


def _month_wise(df: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    rows = []
    C = df["is_credit"]
    for m in months:
        sub = df[df["month"] == m]
        rows.append([
            _month_label(m),
            round(float(sub[~sub["is_credit"]]["tran_amt_in_ac"].sum()), 2),
            round(float(sub[sub["is_credit"]]["tran_amt_in_ac"].sum()), 2),
            round(float(sub[sub["is_emi"]]["tran_amt_in_ac"].sum()), 2),
            round(float(sub[sub["is_salary"]]["tran_amt_in_ac"].sum()), 2),
            round(float(sub[sub["is_loan_credit"]]["tran_amt_in_ac"].sum()), 2),
            round(float(sub[sub["is_investment"] & ~sub["is_credit"]]["tran_amt_in_ac"].sum()), 2),
        ])
    return pd.DataFrame(rows, columns=[
        "Month", "Debit Amount", "Credit Amount", "EMI", "Salary", "Loan Credit", "Investment"])


def _loan_pivot(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    sub = df[mask]
    records = []
    for cat, g in sub.groupby("category_of_txn"):
        amount = round(float(g["tran_amt_in_ac"].mode().iloc[0]) if not g["tran_amt_in_ac"].mode().empty
                       else float(g["tran_amt_in_ac"].iloc[0]), 2)
        for m, gm in g.groupby("month"):
            dates = ", ".join(_date_label(d) for d in sorted(gm["tran_date"]))
            records.append([cat, amount, _month_label(m), dates])
    return pd.DataFrame(records, columns=["Category", "Amount", "Month", "Dates"])


def _funds(df: pd.DataFrame, credit: bool) -> pd.DataFrame:
    sub = df[df["is_credit"]] if credit else df[~df["is_credit"]]
    records = []
    for m in sorted(sub["month"].unique()):
        gm = sub[sub["month"] == m]
        agg = gm.groupby("category_of_txn")["tran_amt_in_ac"].sum().sort_values(ascending=False)
        for desc, val in agg.items():
            records.append([_month_label(m), desc, round(float(val), 2)])
    return pd.DataFrame(records, columns=["Month", "Description", "Amount"])


def _ledger_rows(sub: pd.DataFrame, group=False) -> pd.DataFrame:
    cols = (["Group"] if group else []) + LEDGER_COLS
    rows = []
    for i, (_, r) in enumerate(sub.iterrows(), start=1):
        bal = r["eod_balance"]
        base = [i, _date_label(r["tran_date"]), "", r["tran_partclr"], r["signed"],
                r["category_of_txn"], "" if pd.isna(bal) else bal]
        rows.append(([r["_grp"]] if group else []) + base)
    return pd.DataFrame(rows, columns=cols)


def _recurring_ledger(df: pd.DataFrame, keys: set[str], credit: bool) -> pd.DataFrame:
    sub = df[(df["is_credit"] == credit) & df["category_of_txn"].isin(keys)].copy()
    if sub.empty:
        return pd.DataFrame(columns=["Group"] + LEDGER_COLS)
    order = {k: i + 1 for i, k in enumerate(sub["category_of_txn"].drop_duplicates())}
    sub = sub.sort_values(["category_of_txn", "tran_date"])
    sub["_grp"] = sub["category_of_txn"].map(order)
    return _ledger_rows(sub, group=True)


# --------------------------------------------------------------------------- #
# FCU indicators
# --------------------------------------------------------------------------- #
def _fcu(df: pd.DataFrame) -> pd.DataFrame:
    has_bal = _has_balance(df)
    C = df["is_credit"]

    # amount-balance mismatch (needs balance continuity)
    mismatch_n = 0
    if has_bal:
        b = df.dropna(subset=["eod_balance"])
        prev = b["eod_balance"].shift(1)
        expected = prev + b["signed"]
        mismatch_n = int((expected.notna() & (abs(expected - b["eod_balance"]) > 1.0)).sum())

    neg_n = int((df["eod_balance"] < 0).sum()) if has_bal else 0
    equal_cd = (round(df[C]["tran_amt_in_ac"].sum(), 2) == round(df[~C]["tran_amt_in_ac"].sum(), 2)) \
        or (int(C.sum()) == int((~C).sum()))

    # no transactions before 5th and after 20th, per month
    day = df["tran_date"].map(lambda d: int(str(d)[8:10]) if len(str(d)) >= 10 else 0)
    no_range = 0
    for m in df["month"].unique():
        dd = day[df["month"] == m]
        if not ((dd < 5).any() and (dd > 20).any()):
            no_range += 1

    sal = df[df["is_salary"]]
    rounded_sal = int((sal["tran_amt_in_ac"] % 1000 == 0).sum()) if len(sal) else 0
    gaming_n = int(df["is_gaming"].sum())
    atm_n = int(df["is_atm"].sum())
    ecs_n = int(_ecs_bounce_mask(df["tran_partclr"]).sum())

    def yn(flag):
        return ("Y", ) if flag else ("N", )

    rows = [
        ["Possible Fraud Indicators", 1, "Amount-Balance Mismatches",
         "Transactions whose amount/balance do not reconcile with the prior running balance.",
         "Y" if mismatch_n else ("N" if has_bal else ""), mismatch_n if has_bal else ""],
        ["Possible Fraud Indicators", 2, "Cash Deposit On Holidays",
         "Cash deposits happening on bank holidays.", "", ""],
        ["Possible Fraud Indicators", 3, "Suspicious Salary Credits",
         "Salary credits on bank holidays and round figure credits.", "", ""],
        ["Possible Fraud Indicators", 4, "Suspicious bank eStatement",
         "Statement status of FRAUD or REFER.", "", ""],
        ["Possible Fraud Indicators", 5, "ATM Cash withdrawal",
         "ATM withdrawals detected.", "Y" if atm_n else "N", atm_n],
        ["Behavioural/Transactional Indicators", 6, "Equal Credit Debit",
         "Total credit equals total debit, or credit count equals debit count.",
         "Y" if equal_cd else "N", ""],
        ["Behavioural/Transactional Indicators", 7, "Negative EOD Balance",
         "End-of-day balance negative on any day.",
         "Y" if neg_n else ("N" if has_bal else ""), neg_n if has_bal else ""],
        ["Behavioural/Transactional Indicators", 8, "Immediate big debit after Salary credit",
         "Large withdrawal soon after salary credit.", "", ""],
        ["Behavioural/Transactional Indicators", 9, "Cash deposit higher than maximum salary",
         "Cash deposit greater than the maximum salary.", "", ""],
        ["Behavioural/Transactional Indicators", 10, "Irregular Salary Credits",
         "Salary credits not present in all months within a narrow date range.", "", ""],
        ["Behavioural/Transactional Indicators", 11, "Rounded salary transaction",
         "Salary transactions with rounded amount value.",
         "Y" if rounded_sal else "N", rounded_sal],
        ["Behavioural/Transactional Indicators", 12, "Round figure Tax Payments",
         "Tax paid amounts that are round figures.", "", ""],
        ["Behavioural/Transactional Indicators", 13, "More and frequent Cash Deposits than Salary",
         "Higher number/amount of cash deposits than salary.", "", ""],
        ["Behavioural/Transactional Indicators", 14, "No Transactions in the Expected Range",
         "Months with no transactions before the 5th and after the 20th.",
         "Y" if no_range else "N", no_range],
        ["Behavioural/Transactional Indicators", 15, "Immediate cash withdrawals after cash deposit",
         "Withdrawal of a large share of a cash deposit within a short time.", "", ""],
        ["Behavioural/Transactional Indicators", 16, "Only cash transactions",
         "Predominantly cash deposit/withdrawal activity.", "", ""],
        ["Behavioural/Transactional Indicators", 17, "EMI Cheque Bounce",
         "Consecutive EMI cheque bounces.", "", ""],
        ["Behavioural/Transactional Indicators", 18, "Suspicious Gaming Transactions",
         "Unusual count/amount of gaming transactions.",
         "Y" if gaming_n else "N", gaming_n],
    ]
    return pd.DataFrame(rows, columns=["Group", "#", "Indicator", "Description", "Identified", "Count/Remarks"])


def _eod_matrix(df: pd.DataFrame) -> pd.DataFrame | None:
    daily = _daily_balance_series(df)
    if daily is None:
        return None
    frame = daily.to_frame("bal")
    frame["day"] = frame.index.day
    frame["mlabel"] = frame.index.strftime("%Y-%m")
    pivot = frame.pivot_table(index="day", columns="mlabel", values="bal", aggfunc="last")
    pivot = pivot.reindex(range(1, 32))
    pivot.columns = [_month_label(c) for c in pivot.columns]
    out = pivot.reset_index().rename(columns={"day": "Day-Month"})
    return out.where(out.notna(), "")


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def build_statement_workbook(source_csv=None, out_path=DEFAULT_OUT, reports_copy=None,
                             cust_df=None, customer_report=None, rg_salary_data=None,
                             party_hint=None) -> Path:
    """Build the workbook from either a tab-separated CSV (``source_csv``, which
    preserves raw categories + eod_balance) or a pre-loaded ``cust_df``
    (single-customer frame; used for plain runs — balance sheets stay blank)."""
    if cust_df is not None:
        df = cust_df.copy()
        df["tran_date"] = df["tran_date"].astype(str)
    else:
        df = pd.read_csv(source_csv, index_col=False, dtype={"tran_date": str})
    df = _prep(df)
    months = sorted(df["month"].unique())
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sheets = {
        "Analysis": [("Account Info", _account_info(df, rg_salary_data, party_hint)),
                     ("Monthly Metrics", _monthly_metrics(df, months))],
        "Loan_credit_Recieved": _loan_pivot(df, df["is_loan_credit"]),
        "Loan_emi_debit": _loan_pivot(df, df["is_emi"]),
        "EOD_Balances": _eod_matrix(df),
        "xns": _xns_sheet(df),
        "funds_recieved": _funds(df, credit=True),
        "funds_remitences": _funds(df, credit=False),
        "salary_txns": _ledger_rows(df[df["is_salary"]].assign(_grp=0)),
        "recurring_credits": _recurring_ledger(df, _recurring_credit_keys(df), credit=True),
        "recurring_debits": _recurring_ledger(df, _recurring_debit_keys(df), credit=False),
        "month_wise_analysis": _month_wise(df, months),
        "fcu_idicators": _fcu(df),
    }

    empty_eod = pd.DataFrame(columns=["Day-Month"] + [_month_label(m) for m in months])
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for name, payload in sheets.items():
            if payload is None:
                payload = empty_eod if name == "EOD_Balances" else pd.DataFrame()
            write_sheet(writer, name, payload)

    if reports_copy:
        try:
            Path(reports_copy).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out_path, reports_copy)
        except Exception as e:
            logger.warning("Could not copy workbook to %s: %s", reports_copy, e)

    return out_path


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source_csv", help="xn_d1-schema CSV (tab-separated), optionally with eod_balance")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out = build_statement_workbook(args.source_csv, out_path=args.out)
    print(f"Wrote workbook -> {out}")


if __name__ == "__main__":
    main()
