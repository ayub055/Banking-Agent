"""
Excel exporter for combined report data.

Usage (single customer):
    row = build_excel_row(customer_id, customer_report,
                          combined_summary, report_path, rg_salary_data)
    export_row_to_excel(row, "reports/excel/100070028.xlsx")

Usage (batch merge):
    merge_excel_reports("reports/excel/", "reports/batch_output.xlsx")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

if TYPE_CHECKING:
    from schemas.customer_report import CustomerReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template column order — matches crn_report_template.csv exactly
# ---------------------------------------------------------------------------
TEMPLATE_COLUMNS = [
    "CRN",
    "offer Amt",
    "Salary Value & Company",
    "Assesement Strength & Quality",
    "Relationship",
    "Event Detector",
    "Summary",
    "Bureau Brief",
    "Banking Breif",
    "Bu & Banking Segment",
    "Max DPD & Product",
    "CC Util",
    "Enquiries",
    "Payments Missed in l 18M",
    "Foir",
    "Exposure Commentary",
    "TU Score",
    "Transaction Red flag",
    "Concerns",
    "Intelligent Report",
]


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def build_excel_row(
    customer_id: int,
    customer_report: Optional[CustomerReport],
    combined_summary: Optional[str],
    report_path: Optional[str],
    rg_salary_data: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Map all available banking report data onto the template columns.

    Columns that have no corresponding data field are set to None so the
    template column still appears in the output file. (Bureau-side columns
    are always None — the bureau pipeline has been removed.)

    Args:
        customer_id:      Customer CRN
        customer_report:  Populated CustomerReport (banking side), may be None
        combined_summary: LLM-generated executive summary text
        report_path:      Filesystem path to the generated HTML report
        rg_salary_data:   Internal salary algorithm output (rg_sal / rg_income)

    Returns:
        Dict keyed by TEMPLATE_COLUMNS values, ready to be written as one row.
    """
    row: Dict[str, Any] = {col: None for col in TEMPLATE_COLUMNS}

    # ── CRN ──────────────────────────────────────────────────────────────────
    row["CRN"] = customer_id

    # ── offer Amt ─────────────────────────────────────────────────────────────
    # Not present in any report — left as None for manual fill
    row["offer Amt"] = None

    # ── Salary Value & Company ────────────────────────────────────────────────
    # Prefer the RG salary algorithm output (more reliable employer name).
    # Fall back to the banking-detected salary block.
    salary_amount: Optional[float] = None
    salary_company: Optional[str] = None

    if rg_salary_data and rg_salary_data.get("rg_sal"):
        rg_sal = rg_salary_data["rg_sal"]
        salary_amount = rg_sal.get("salary_amount")
        salary_company = rg_sal.get("merchant")
    elif customer_report and customer_report.salary:
        salary_amount = customer_report.salary.avg_amount
        # Try to extract employer from narration (e.g. "SYMED SALARY OCT 2025")
        narration = customer_report.salary.narration or ""
        salary_company = narration.split()[0].title() if narration else None

    if salary_amount is not None and salary_company:
        row["Salary Value & Company"] = f"{salary_amount:,.0f} / {salary_company}"
    elif salary_amount is not None:
        row["Salary Value & Company"] = f"{salary_amount:,.0f}"
    else:
        row["Salary Value & Company"] = None

    # ── Assessment Strength & Quality ─────────────────────────────────────────
    # Not computed by the system — placeholder for analyst override.
    row["Assesement Strength & Quality"] = None

    # ── Relationship ──────────────────────────────────────────────────────────
    # Derived from salary detection method (rg_sal.chosen_method).
    # Examples: "SALARY", "PENSION", etc.  Falls back to account_quality type.
    relationship: Optional[str] = None
    if rg_salary_data and rg_salary_data.get("rg_sal"):
        method = rg_salary_data["rg_sal"].get("method", "")
        pension = rg_salary_data["rg_sal"].get("pension_flag", 0)
        if pension:
            relationship = "Pension SAL"
        elif method:
            relationship = f"Corp SAL"  # default label for employed salary
    elif customer_report and customer_report.salary:
        relationship = "Salary"
    row["Relationship"] = relationship

    # ── Event Detector ────────────────────────────────────────────────────────
    # Format detected transaction events as a short readable string.
    if customer_report and customer_report.events:
        event_parts = []
        for ev in customer_report.events:
            month = ev.get("month_label") or ev.get("month", "")
            desc = ev.get("description") or ev.get("event_type", "")
            amt = ev.get("amount")
            amt_str = f"₹{amt:,.0f}" if amt else ""
            event_parts.append(f"{month}: {desc} {amt_str}".strip())
        row["Event Detector"] = " | ".join(event_parts) if event_parts else None
    else:
        row["Event Detector"] = None

    # ── Summary ───────────────────────────────────────────────────────────────
    row["Summary"] = combined_summary

    # ── Bureau Brief (bureau pipeline removed → always None) ──────────────────
    row["Bureau Brief"] = None

    # ── Banking Brief ─────────────────────────────────────────────────────────
    row["Banking Breif"] = (
        customer_report.customer_review if customer_report else None
    )

    # ── Bu & Banking Segment ─────────────────────────────────────────────────
    # Banking side: account_quality.account_type (e.g. "primary", "conduit")
    banking_seg: Optional[str] = None
    if customer_report and customer_report.account_quality:
        banking_seg = (
            customer_report.account_quality.get("account_type", "").title() or None
        )
    row["Bu & Banking Segment"] = banking_seg

    # ── Bureau-only columns (no bureau data) ──────────────────────────────────
    row["Max DPD & Product"] = None
    row["CC Util"] = None
    row["Enquiries"] = None
    row["Payments Missed in l 18M"] = None
    row["Foir"] = None
    row["Exposure Commentary"] = None
    row["TU Score"] = None

    # ── Transaction Red flag ──────────────────────────────────────────────────
    # Total spend in Digital_Betting_Gaming category
    red_flag_amount: Optional[float] = None
    if customer_report and customer_report.category_overview:
        red_flag_amount = customer_report.category_overview.get(
            "Digital_Betting_Gaming", None
        )
    row["Transaction Red flag"] = red_flag_amount

    # ── Concerns (was bureau key findings) ────────────────────────────────────
    row["Concerns"] = None

    # ── Intelligent Report (HTML link) ───────────────────────────────────────
    html_path = report_path.replace(".pdf", ".html") if report_path else None
    row["Intelligent Report"] = html_path

    return row


# ---------------------------------------------------------------------------
# Single-customer Excel writer
# ---------------------------------------------------------------------------

def export_row_to_excel(row: Dict[str, Any], output_path: str) -> str:
    """
    Write a single customer row to an Excel file.

    The file is always created fresh (one row per customer file).
    Use merge_excel_reports() afterwards to combine into one master file.

    Args:
        row:         Dict from build_excel_row()
        output_path: Destination .xlsx path

    Returns:
        Absolute path to the written file
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([row], columns=TEMPLATE_COLUMNS)
    df.to_excel(output_path, index=False)
    logger.info("Excel row written → %s", output_path)
    return os.path.abspath(output_path)


# ---------------------------------------------------------------------------
# Batch merge
# ---------------------------------------------------------------------------

def merge_excel_reports(
    excel_dir: str,
    output_path: str,
    pattern: str = "*.xlsx",
) -> str:
    """
    Merge all per-customer Excel files in excel_dir into one master file.

    Args:
        excel_dir:   Directory containing per-customer .xlsx files
        output_path: Destination for the merged file
        pattern:     Glob pattern for source files (default: *.xlsx)

    Returns:
        Absolute path to the merged file
    """
    source_files = sorted(Path(excel_dir).glob(pattern))
    if not source_files:
        raise FileNotFoundError(f"No Excel files matching '{pattern}' in {excel_dir}")

    frames = [pd.read_excel(f) for f in source_files]
    merged = pd.concat(frames, ignore_index=True)

    # Enforce template column order (add missing cols as empty, drop extras)
    for col in TEMPLATE_COLUMNS:
        if col not in merged.columns:
            merged[col] = None
    merged = merged[TEMPLATE_COLUMNS]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    merged.to_excel(output_path, index=False)
    logger.info(
        "Merged %d customer rows → %s", len(merged), output_path
    )
    return os.path.abspath(output_path)
