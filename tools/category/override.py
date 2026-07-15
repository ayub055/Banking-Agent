"""Admin-driven L2 category corrections for rgs.csv.

The HTML report's admin edit mode (templates/bank_report_v2.html) POSTs a list
of L2 edits to the local serve_report.py endpoint, which calls
``apply_customer_edits`` here. We mutate rgs.csv **only for rows belonging to
the specified customer** so other customers' data is never touched. The L1
column (category_of_txn) is always re-derived from L2 via ``l1_of``.

Matching strategy for each edit:
    (cust_id == customer_id)
    & (tran_date == edit.date)            # YYYY-MM-DD string
    & (tran_amt_in_ac == edit.amount)     # float compare with small tolerance
    & (tran_partclr == edit.narration)    # exact string match after strip

Writes are atomic (write temp file + os.replace) so a crash mid-write cannot
corrupt rgs.csv. After a successful write, the in-memory loader cache is
invalidated so the next report regeneration picks up the change.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

import pandas as pd

from config.settings import TRANSACTIONS_FILE, TRANSACTIONS_DELIMITER
from tools.category.registry import l1_of, l2_canonical

logger = logging.getLogger(__name__)


def apply_customer_edits(customer_id: int, edits: list[dict[str, Any]]) -> int:
    """Apply L2 category overrides for ``customer_id`` and rewrite rgs.csv.

    Args:
        customer_id: CRN whose rows are eligible for mutation.
        edits: list of ``{date, amount, narration, new_l2}`` dicts from the UI.

    Returns:
        Number of rows whose category_of_txn_l2 changed.
    """
    if not edits:
        return 0

    df = pd.read_csv(TRANSACTIONS_FILE, sep=TRANSACTIONS_DELIMITER, index_col=False, dtype=str)

    # Coerce only the columns we need to compare numerically.
    amt_numeric = pd.to_numeric(df["tran_amt_in_ac"], errors="coerce")
    cust_numeric = pd.to_numeric(df["cust_id"], errors="coerce")

    customer_mask = cust_numeric == int(customer_id)
    if not customer_mask.any():
        logger.warning("apply_customer_edits: no rows for cust_id=%s", customer_id)
        return 0

    total_changed = 0
    for edit in edits:
        new_l2_canonical = l2_canonical(edit.get("new_l2"))
        if not new_l2_canonical:
            logger.warning("Skipping edit with unmappable L2: %r", edit.get("new_l2"))
            continue

        try:
            edit_amount = float(edit.get("amount") or 0)
        except (TypeError, ValueError):
            logger.warning("Skipping edit with bad amount: %r", edit)
            continue
        edit_date = str(edit.get("date") or "").strip()
        edit_narr = str(edit.get("narration") or "").strip()
        if not edit_date or not edit_narr:
            logger.warning("Skipping edit missing date/narration: %r", edit)
            continue

        mask = (
            customer_mask
            & (df["tran_date"].astype(str).str.strip() == edit_date)
            & ((amt_numeric - edit_amount).abs() < 0.01)
            & (df["tran_partclr"].astype(str).str.strip() == edit_narr)
        )
        n = int(mask.sum())
        if n == 0:
            logger.warning(
                "No row matched edit: cust=%s date=%s amt=%s narr=%r",
                customer_id, edit_date, edit_amount, edit_narr,
            )
            continue
        df.loc[mask, "category_of_txn_l2"] = new_l2_canonical
        df.loc[mask, "category_of_txn"] = l1_of(new_l2_canonical)
        total_changed += n

    if total_changed == 0:
        return 0

    # Atomic write: temp file in same directory, then os.replace.
    target_dir = os.path.dirname(TRANSACTIONS_FILE) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".rgs_", suffix=".csv.tmp", dir=target_dir)
    os.close(fd)
    try:
        df.to_csv(tmp_path, sep=TRANSACTIONS_DELIMITER, index=False)
        os.replace(tmp_path, TRANSACTIONS_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    # Bust the loader cache so a subsequent in-process load sees the new data.
    try:
        import data.loader as _loader
        _loader._transactions_df = None
    except Exception:
        pass

    logger.info("apply_customer_edits: cust=%s changed %d row(s)", customer_id, total_changed)
    return total_changed
