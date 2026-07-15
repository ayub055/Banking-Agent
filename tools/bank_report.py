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
) -> Tuple[Optional[CustomerReport], str]:
    """Build a banking-only CustomerReport and render it.

    Returns:
        Tuple of (CustomerReport | None, output_path).
    """
    from pipeline.reports.customer_report_builder import build_customer_report
    from pipeline.reports.report_summary_chain import generate_customer_review
    from pipeline.renderers.combined_report_renderer import render_combined_report
    from data.loader import load_rg_salary_data

    customer_report: Optional[CustomerReport] = None
    try:
        customer_report = build_customer_report(customer_id)
    except Exception as e:
        logger.warning(f"Banking report build failed for {customer_id}: {e}")

    rg_salary_data = None
    try:
        rg_salary_data = load_rg_salary_data(customer_id) or None
    except Exception as e:
        logger.warning(f"RG salary data unavailable for [{customer_id}]: {e}")

    if customer_report and customer_report.meta.transaction_count >= 10:
        try:
            customer_report.customer_review = generate_customer_review(
                customer_report, rg_salary_data=rg_salary_data,
            )
        except Exception as e:
            logger.warning(f"customer_review generation failed: {e}")

    narrative = customer_report.customer_review if customer_report else None
    output_path = f"reports/customer_{customer_id}_report_v2.html"
    out = render_combined_report(
        customer_report,
        output_path=output_path,
        combined_summary=narrative,
        rg_salary_data=rg_salary_data,
        theme=theme,
    )

    try:
        from tools.excel_exporter import build_excel_row, export_row_to_excel
        row = build_excel_row(
            customer_id=customer_id,
            customer_report=customer_report,
            combined_summary=None,
            report_path=out,
            rg_salary_data=rg_salary_data,
        )
        excel_path = os.path.join(_EXCEL_OUTPUT_DIR, f"{customer_id}.xlsx")
        export_row_to_excel(row, excel_path)
    except Exception as exc:
        logger.warning("Excel export failed for %s: %s", customer_id, exc)

    return customer_report, out
