"""Banking report renderer — turns a CustomerReport into the bank_v2 HTML report.

NO LLM calls — NO data manipulation — just rendering (plus the deterministic
checklist computation consumed by the bank_v2 view model).
"""

from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader


from schemas.customer_report import CustomerReport
from utils.helpers import mask_customer_id, format_inr, format_inr_units, strip_segment_prefix


def render_combined_report(
    customer_report: Optional[CustomerReport],
    output_path: Optional[str] = None,
    combined_summary: Optional[str] = None,
    rg_salary_data: Optional[dict] = None,
    theme: str = "bank_v2",
) -> str:
    """Render the banking HTML report and write it to disk.

    Args:
        customer_report: Fully populated CustomerReport, or None if unavailable.
        output_path: Desired output path; a legacy ``.pdf`` suffix is normalised
            to ``.html``. Defaults to reports/combined_{customer_id}_report.html.
        combined_summary: LLM-generated executive summary.
        rg_salary_data: Optional internal salary algorithm data dict.

    Returns:
        Path where the HTML was saved.
    """
    if output_path is None:
        cid = customer_report.meta.customer_id if customer_report else "unknown"
        output_path = f"reports/combined_{cid}_report.html"

    html_path = str(Path(output_path)).replace(".pdf", ".html")
    output_file = Path(html_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    html_content = render_combined_report_html(
        customer_report, combined_summary=combined_summary,
        rg_salary_data=rg_salary_data, theme=theme,
    )
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Also copy HTML to dedicated combined_report_html_version folder
    html_version_dir = output_file.parent / "combined_report_html_version"
    html_version_dir.mkdir(parents=True, exist_ok=True)
    html_version_path = html_version_dir / output_file.name
    with open(str(html_version_path), "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_path


def render_combined_report_html(
    customer_report: Optional[CustomerReport],
    combined_summary: Optional[str] = None,
    rg_salary_data: Optional[dict] = None,
    theme: str = "bank_v2",
) -> str:
    """Render the banking HTML report using the canonical bank_v2 template.

    Args:
        theme: Retained for call-site compatibility; only bank_v2 is supported.

    Returns:
        HTML string.
    """
    template_dir = Path(__file__).parent.parent.parent / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=True,
    )
    env.filters["mask_id"] = mask_customer_id
    env.filters["inr"] = format_inr
    env.filters["inr_units"] = format_inr_units
    env.filters["segment"] = strip_segment_prefix

    from tools.scorecard import compute_scorecard
    scorecard = compute_scorecard(customer_report=customer_report, rg_salary_data=rg_salary_data)
    if combined_summary:
        scorecard["narrative"] = combined_summary

    bank_v2_ctx = None
    try:
        from pipeline.renderers.bank_v2_view_model import build_bank_v2_context
        bank_v2_ctx = build_bank_v2_context(
            customer_report=customer_report,
            scorecard=scorecard,
            rg_salary_data=rg_salary_data,
            combined_summary=combined_summary,
        )
    except Exception:
        bank_v2_ctx = None

    template = env.get_template("bank_report_v2.html")
    return template.render(bank_v2=bank_v2_ctx)
