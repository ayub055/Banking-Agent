"""Ingest an *xns* statement file into an ``xn_d1.csv``-compatible per-customer
dataset (tab-separated), carrying the running Balance across as ``eod_balance``.

The canonical ``data/xn_d1.csv`` is never touched. Output goes to
``data/xns_generated/<cust>_xn_d1.csv`` so the pipeline can be pointed at it
(via ``data.loader.load_transactions(path=...)``) without any downstream change.

Accepted input:
  * an .xlsx workbook containing an ``xns`` sheet (else the first sheet), or
  * a .csv / .tsv with the xns headers
    (Sl.No, Date, Cheque No, Description, Amount, Category, Balance).

Output columns (xn_d1 schema + balance):
  cust_id, dr_cr_indctor, tran_date, prty_name, tran_amt_in_ac, tran_partclr,
  sal_flag, self_transfer, tran_type, category_of_txn, category_of_txn_l2,
  eod_balance

Run:  python -m tools.xns_ingest <xns_file> --cust-id <id> [--party "Name"]
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import date, datetime
from pathlib import Path

from tools.excel_utils import clean_number

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "data" / "xns_generated"

OUT_COLUMNS = [
    "cust_id", "dr_cr_indctor", "tran_date", "prty_name", "tran_amt_in_ac",
    "tran_partclr", "sal_flag", "self_transfer", "tran_type",
    "category_of_txn", "category_of_txn_l2", "eod_balance",
]

SELF_KEYWORDS = ("OWN A/C", "OWN ACCOUNT", " OWN ", "/OWN ", "SELF")

# header label -> canonical field
_ALIASES = {
    "sl.no": "sl_no", "sl. no.": "sl_no", "sl no": "sl_no", "s.no": "sl_no",
    "date": "date", "tran_date": "date",
    "cheque no": "cheque", "cheque no.": "cheque", "chq no": "cheque",
    "description": "desc", "narration": "desc", "particulars": "desc",
    "amount": "amount", "amt": "amount",
    "category": "category", "cat": "category",
    "balance": "balance", "bal": "balance",
}


def infer_tran_type(desc: str) -> str:
    d = (desc or "").upper()
    if d.startswith("UPI") or "UPI/" in d:
        return "UPI"
    if d.startswith("MB:"):
        return "MB"
    if "IMPS" in d:
        return "IMPS"
    if "NACH" in d:
        return "NACH"
    if d.startswith("NEFT") or " NEFT " in d:
        return "NEFT"
    if d.startswith(("ATL/", "ATI/", "ATW")) or "ATM" in d:
        return "ATM"
    return ""


def to_iso_date(raw) -> str:
    """Normalise a date to 'YYYY-MM-DD'.

    Handles text ('01-Feb-26', '01/02/2026'), real datetime/date cells, and
    datetime strings carrying a time part ('2026-02-01 00:00:00'). Indian
    statements are day-first. Returns the input unchanged if unparseable.
    """
    if raw is None:
        return ""
    if isinstance(raw, (datetime, date)):
        return raw.strftime("%Y-%m-%d")
    s = str(raw).strip()
    if not s:
        return ""
    # drop a trailing time component ('2026-02-01 00:00:00' / '2026-02-01T..')
    s = re.split(r"[ T]", s, 1)[0] if re.match(r"^\d{4}-\d{2}-\d{2}[ T]", s) else s
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # last-resort: pandas' flexible parser (day-first for Indian statements)
    try:
        import pandas as pd
        ts = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
    except Exception:
        pass
    return s


def fmt_amount(amt) -> str:
    a = abs(amt)
    return str(int(a)) if float(a).is_integer() else str(a)


def _read_table(xns_path: Path):
    """Return (list-of-rows, account_name, account_no) from an xlsx or csv xns file."""
    name = acct = ""
    if xns_path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(xns_path, read_only=True, data_only=True)
        sheet = "xns" if "xns" in wb.sheetnames else wb.sheetnames[0]

        def _fmt(c):
            if c is None:
                return ""
            if isinstance(c, (datetime, date)):     # real Excel date cell
                return c.strftime("%Y-%m-%d")
            return str(c).strip()

        rows = [[_fmt(c) for c in r] for r in wb[sheet].iter_rows(values_only=True)]
        if "Analysis" in wb.sheetnames:
            for r in wb["Analysis"].iter_rows(values_only=True):
                k = str(r[0]).strip() if r and r[0] else ""
                v = str(r[1]).strip() if len(r) > 1 and r[1] else ""
                if k == "Name":
                    name = v
                elif k == "Account No.":
                    acct = v
        return rows, name, acct
    # csv / tsv: sniff delimiter from the first non-empty line
    with open(xns_path, newline="") as fh:
        sample = fh.readline()
    delim = "\t" if sample.count("\t") >= sample.count(",") else ","
    with open(xns_path, newline="") as fh:
        rows = [[c.strip() for c in r] for r in csv.reader(fh, delimiter=delim)]
    return rows, name, acct


def _locate_columns(rows):
    """Find the header row and map canonical field -> column index."""
    for i, r in enumerate(rows):
        low = [c.lower() for c in r]
        if "description" in low or "narration" in low or "particulars" in low:
            idx = {}
            for j, label in enumerate(low):
                key = _ALIASES.get(label)
                if key and key not in idx:
                    idx[key] = j
            if "desc" in idx and "amount" in idx:
                return i, idx
    raise ValueError("Could not locate an xns header row (need Description + Amount).")


def ingest_xns(xns_path, cust_id=None, party=None) -> Path:
    """Convert an xns file to an xn_d1-compatible CSV. Returns the output path."""
    xns_path = Path(xns_path)
    rows, name, acct = _read_table(xns_path)
    hdr, idx = _locate_columns(rows)

    cid = str(cust_id or acct or xns_path.stem)
    pty = party or name or ""

    def get(r, key):
        j = idx.get(key)
        return r[j].strip() if j is not None and j < len(r) else ""

    out_rows = []
    for r in rows[hdr + 1:]:
        raw_amt = get(r, "amount")
        date = get(r, "date")
        amount = clean_number(raw_amt)
        if amount is None or not isinstance(amount, (int, float)) or not date:
            continue
        desc = get(r, "desc")
        category = get(r, "category")
        balance = clean_number(get(r, "balance"))
        out_rows.append([
            cid,
            "D" if float(amount) < 0 else "C",
            to_iso_date(date),
            pty,
            fmt_amount(amount),
            desc,
            "1" if "salary" in category.lower() else "",
            "1" if any(k in desc.upper() for k in SELF_KEYWORDS) else "0",
            infer_tran_type(desc),
            category,
            category,
            "" if balance is None else balance,
        ])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{cid}_xn_d1.csv"
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(OUT_COLUMNS)
        w.writerows(out_rows)
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xns_file", help="path to an xns .xlsx or .csv/.tsv")
    ap.add_argument("--cust-id", default=None)
    ap.add_argument("--party", default=None)
    args = ap.parse_args()

    out = ingest_xns(args.xns_file, cust_id=args.cust_id, party=args.party)
    with open(out) as fh:
        n = sum(1 for _ in fh) - 1
    print(f"Wrote {n} rows -> {out}")


if __name__ == "__main__":
    main()
