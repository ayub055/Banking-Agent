"""Shared test-suite evaluator for the salary extractors.

Each folder's ``test_suite.py`` is a thin wrapper that calls :func:`run` with
its extractor. ``run`` globs the two parquet inputs from the folder, executes
the extractor, matches predictions against the crn-level master, and prints an
overall + band-wise accuracy report.

Contract (per folder):
  - transactions parquet: ``*txn*.parquet`` (fallback ``*transaction*.parquet``)
  - master parquet:       ``*master*.parquet`` with columns ``crn`` + ``salary``
Match rule: predicted within ``tol`` (default +/-10%) of actual == matched.
"""

from glob import glob
from pathlib import Path

import pandas as pd

# Band edges on the *actual* monthly salary (INR). Last band is open-ended.
_BANDS = [0, 20_000, 40_000, 60_000, 100_000, float("inf")]
_BAND_LABELS = ["0-20k", "20k-40k", "40k-60k", "60k-100k", "100k+"]


def _find(folder: Path, *patterns: str) -> str:
    for pat in patterns:
        hits = sorted(glob(str(folder / pat)))
        if hits:
            return hits[0]
    raise FileNotFoundError(f"no {patterns[0]} found in {folder}")


def _classify(ratio: float, tol: float) -> str:
    if pd.isna(ratio):
        return "missing"
    if ratio > 1 + tol:
        return "overestimate"
    if ratio < 1 - tol:
        return "underestimate"
    return "matched"


def run(folder, extract_fn, key_col: str, val_col: str, tol: float = 0.10) -> pd.DataFrame:
    folder = Path(folder)
    txn_path = _find(folder, "*txn*.parquet", "*transaction*.parquet")
    master_path = _find(folder, "*master*.parquet")
    print(f"[{folder.name}] txns={Path(txn_path).name}  master={Path(master_path).name}")

    pred = extract_fn(pd.read_parquet(txn_path))
    pred = pred[[key_col, val_col]].drop_duplicates(key_col)
    pred[key_col] = pred[key_col].astype(str)

    master = pd.read_parquet(master_path)[["crn", "salary"]].copy()
    master["crn"] = master["crn"].astype(str)

    m = master.merge(pred, left_on="crn", right_on=key_col, how="left")
    m["predicted"] = pd.to_numeric(m[val_col], errors="coerce")
    m["actual"] = pd.to_numeric(m["salary"], errors="coerce")
    m.loc[m["predicted"].fillna(0) <= 0, "predicted"] = pd.NA
    m["ratio"] = m["predicted"] / m["actual"]
    m["outcome"] = m["ratio"].apply(lambda r: _classify(r, tol))
    m["abs_pct_err"] = (m["predicted"] - m["actual"]).abs() / m["actual"] * 100
    m["band"] = pd.cut(m["actual"], bins=_BANDS, labels=_BAND_LABELS, right=False)

    total = len(m)
    counts = m["outcome"].value_counts()
    matched = counts.get("matched", 0)
    print(f"\nCustomers in master: {total}")
    for k in ("matched", "overestimate", "underestimate", "missing"):
        c = counts.get(k, 0)
        print(f"  {k:<14} {c:>5}  ({c / total:6.1%})")
    print(f"  match rate     {matched / total:6.1%}   "
          f"MAPE (scored): {m['abs_pct_err'].mean():.1f}%")

    print("\nBand-wise (by actual salary):")
    print(f"  {'band':<10}{'n':>5}{'matched':>9}{'over':>6}{'under':>7}"
          f"{'miss':>6}{'match%':>9}{'MAPE%':>8}")
    for label in _BAND_LABELS:
        b = m[m["band"] == label]
        n = len(b)
        if not n:
            continue
        bc = b["outcome"].value_counts()
        mt = bc.get("matched", 0)
        print(f"  {label:<10}{n:>5}{mt:>9}{bc.get('overestimate', 0):>6}"
              f"{bc.get('underestimate', 0):>7}{bc.get('missing', 0):>6}"
              f"{mt / n:>8.0%}{b['abs_pct_err'].mean():>8.1f}")
    return m
