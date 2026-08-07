"""Banking-only report entry point.

Builds a CustomerReport from rgs.csv, runs LLM narration, and renders
the banking-only HTML using the canonical `bank_report_v2.html` template.
"""

import logging
import os
from typing import Optional, Tuple

from schemas.customer_report import CustomerReport

logger = logging.getLogger(__name__)

_EXCEL_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reports", "excel",
)


def generate_bank_report(
    customer_id: int,
    theme: str = "bank_v2",
    statement_source: Optional[str] = None,
) -> Tuple[Optional[CustomerReport], str]:
    """Build a banking-only CustomerReport and render it.

    ``statement_source`` is the per-customer CSV generated from an xns file
    (raw categories + eod_balance); when given, the 12-sheet workbook is built
    from it. Otherwise the workbook is built from the loaded transactions
    (balance-dependent sheets stay blank).

    Returns a tuple of (CustomerReport | None, output_path).
    """
    from pipeline.reports.customer_report_builder import build_customer_report
    from pipeline.reports.report_summary_chain import generate_customer_review
    from pipeline.renderers.combined_report_renderer import render_combined_report
    from data.loader import load_rg_salary_data

    # Load the salary-algorithm output once; it is threaded through the
    # builder (account quality + event detection) and reused for narration,
    # rendering, and the Excel export below.
    rg_salary_raw = None
    try: rg_salary_raw = load_rg_salary_data(customer_id)
    except Exception as e: logger.warning(f"RG salary data unavailable for [{customer_id}]: {e}")
    rg_salary_data = rg_salary_raw or None

    customer_report: Optional[CustomerReport] = None
    try: customer_report = build_customer_report(customer_id, rg_salary_data=rg_salary_data)
    except Exception as e: logger.warning(f"Banking report build failed for {customer_id}: {e}")

    if customer_report and customer_report.meta.transaction_count >= 10:
        try: customer_report.customer_review = generate_customer_review(customer_report, rg_salary_data=rg_salary_data)
        except Exception as e: logger.warning(f"customer_review generation failed: {e}")

    narrative = customer_report.customer_review if customer_report else None

    # Build the full 12-sheet statement workbook FIRST so its bytes can be
    # embedded (base64) into the HTML — the "Download as Excel" button then works
    # straight from the HTML file, with no server or sibling file needed. The
    # workbook is also written to disk (named file + a reports/ copy).
    workbook_b64 = None
    try:
        import base64
        from tools.statement_workbook import build_statement_workbook, DEFAULT_OUT
        reports_copy = os.path.join("reports", "bank_statement_analysis.xlsx")
        if statement_source:
            wb_path = build_statement_workbook(source_csv=statement_source, out_path=DEFAULT_OUT,
                                               reports_copy=reports_copy, customer_report=customer_report,
                                               rg_salary_data=rg_salary_data)
        else:
            from data.loader import load_transactions
            df = load_transactions()
            cust_df = df[df["cust_id"] == customer_id].copy()
            wb_path = build_statement_workbook(cust_df=cust_df, out_path=DEFAULT_OUT,
                                               reports_copy=reports_copy, customer_report=customer_report,
                                               rg_salary_data=rg_salary_data)
        with open(wb_path, "rb") as fh:
            workbook_b64 = base64.b64encode(fh.read()).decode("ascii")
    except Exception as e: logger.warning(f"Statement workbook build failed for [{customer_id}]: {e}")

    output_path = f"reports/customer_{customer_id}_report_v2.html"
    out = render_combined_report(customer_report, output_path=output_path, combined_summary=narrative,
                                 rg_salary_data=rg_salary_data, theme=theme, excel_workbook_b64=workbook_b64)

    try:
        from tools.excel_exporter import build_excel_row, export_row_to_excel
        row = build_excel_row(customer_id=customer_id, customer_report=customer_report, combined_summary=None, report_path=out, rg_salary_data=rg_salary_data)
        excel_path = os.path.join(_EXCEL_OUTPUT_DIR, f"{customer_id}.xlsx")
        export_row_to_excel(row, excel_path)
    except Exception as e: logger.warning(f"Excel export failed for [{customer_id}]: {e}")

    return customer_report, out
