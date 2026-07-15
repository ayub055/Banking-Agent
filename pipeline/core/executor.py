"""Tool executor - runs tools and collects structured results."""

from typing import List, Dict, Any
from schemas.response import ToolResult
from tools import analytics
from tools.category.resolver import category_presence_lookup
from tools.bank_report import generate_bank_report


def _generate_customer_report_with_pdf(customer_id: int, **kwargs) -> Dict[str, Any]:
    """Generate the customer report via the canonical direct builder path."""
    report, report_path = generate_bank_report(customer_id)
    result = report.model_dump()
    result['pdf_path'] = report_path
    result['populated_sections'] = report.get_populated_sections()
    return result


class ToolExecutor:
    def __init__(self):
        self.tool_map = {
            "debit_total": analytics.debit_total,
            "get_total_income": analytics.get_total_income,
            "get_spending_by_category": analytics.get_spending_by_category,
            "top_spending_categories": analytics.top_spending_categories,
            "spending_in_date_range": analytics.spending_in_date_range,
            "list_customers": analytics.list_customers,
            "list_categories": analytics.list_categories,

            "get_credit_statistics": analytics.get_credit_statistics,
            "get_debit_statistics": analytics.get_debit_statistics,
            "get_transaction_counts": analytics.get_transaction_counts,
            "get_balance_trend": analytics.get_balance_trend,
            "detect_anomalies": analytics.detect_anomalies,
            "get_income_stability": analytics.get_income_stability,
            "get_cash_flow": analytics.get_cash_flow,
            "generate_customer_report": _generate_customer_report_with_pdf,
            "generate_lender_profile": analytics.generate_lender_profile,

            "category_presence_lookup": category_presence_lookup,
        }

    def execute(self, plan: List[Dict[str, Any]]) -> List[ToolResult]:
        results = []

        for step in plan:
            tool_name = step["tool"]
            args = step["args"]

            try:
                if tool_name not in self.tool_map:
                    results.append(ToolResult(
                        tool_name=tool_name,
                        args=args,
                        result={},
                        success=False,
                        error=f"Unknown tool: {tool_name}"
                    ))
                    continue

                result = self.tool_map[tool_name](**args)
                results.append(ToolResult(
                    tool_name=tool_name,
                    args=args,
                    result=result,
                    success=True
                ))

            except Exception as e:
                results.append(ToolResult(
                    tool_name=tool_name,
                    args=args,
                    result={},
                    success=False,
                    error=str(e)
                ))

        return results
