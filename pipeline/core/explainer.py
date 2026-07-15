"""Response explainer - generates natural language from structured results."""

import time
from typing import List, Dict, Any, Iterator, Optional
from langchain_ollama import ChatOllama

from schemas.intent import ParsedIntent
from schemas.response import ToolResult
from schemas.transaction_insights import TransactionInsights
from utils.helpers import mask_customer_id
from utils.llm_utils import strip_think, stream_strip_think
from config.settings import EXPLAINER_MODEL, LLM_TEMPERATURE, LLM_SEED, STREAM_DELAY
from config.prompts import EXPLAINER_PROMPT


class ResponseExplainer:
    def __init__(self, model_name: str = EXPLAINER_MODEL, stream_delay: float = STREAM_DELAY):
        """
        Initialize the explainer.

        Args:
            model_name: Ollama model to use
            stream_delay: Delay in seconds between streaming chunks (0.0 = no delay)
                         Use 0.02-0.05 for readable typing effect
        """
        self.llm = ChatOllama(model=model_name, temperature=LLM_TEMPERATURE, seed=LLM_SEED)
        self.stream_delay = stream_delay

    def explain(
        self,
        intent: ParsedIntent,
        results: List[ToolResult],
        transaction_insights: Optional[TransactionInsights] = None
    ) -> str:
        if not results:
            return "No data available to answer this question."

        all_failed = all(not r.success for r in results)
        if all_failed:
            errors = [r.error for r in results if r.error]
            return f"Unable to retrieve data: {'; '.join(errors)}"

        data_str = self._format_results(results)

        if transaction_insights and transaction_insights.patterns:
            insights_section = transaction_insights.to_explainer_context()
            if insights_section:
                data_str = f"{data_str}\n\n{insights_section}"

        prompt = EXPLAINER_PROMPT.format(query=intent.raw_query, data=data_str)

        response = self.llm.invoke(prompt)
        return strip_think(response.content, label="Explainer")

    def stream_explain(
        self,
        intent: ParsedIntent,
        results: List[ToolResult],
        transaction_insights: Optional[TransactionInsights] = None
    ) -> Iterator[str]:
        """
        Stream explanation tokens as they are generated.
        Yields individual tokens/chunks from the LLM.
        """
        if not results:
            yield "No data available to answer this question."
            return

        all_failed = all(not r.success for r in results)
        if all_failed:
            errors = [r.error for r in results if r.error]
            yield f"Unable to retrieve data: {'; '.join(errors)}"
            return

        data_str = self._format_results(results)

        if transaction_insights and transaction_insights.patterns:
            insights_section = transaction_insights.to_explainer_context()
            if insights_section:
                data_str = f"{data_str}\n\n{insights_section}"

        prompt = EXPLAINER_PROMPT.format(query=intent.raw_query, data=data_str)

        # Stream tokens from LLM — strip think block transparently
        def _raw_chunks():
            for chunk in self.llm.stream(prompt):
                if hasattr(chunk, "content") and chunk.content:
                    yield chunk.content
                elif isinstance(chunk, str):
                    yield chunk

        for text in stream_strip_think(_raw_chunks(), label="Explainer"):
            yield text
            if self.stream_delay > 0:
                time.sleep(self.stream_delay)

    def _format_results(self, results: List[ToolResult]) -> str:
        lines = []
        for r in results:
            if r.success:
                data = r.result
                # Special formatting for category presence lookup
                if r.tool_name == "category_presence_lookup" and "present" in data:
                    lines.append(self._format_category_presence(data))
                elif r.tool_name == "generate_customer_report" and "pdf_path" in data:
                    lines.append(self._format_customer_report(data))
                else:
                    lines.append(f"{r.tool_name}: {r.result}")
        return "\n".join(lines)

    def _format_category_presence(self, data: Dict[str, Any]) -> str:
        """Format category presence result with transactions."""
        lines = []
        category = data.get('category', 'Unknown')
        present = data.get('present', False)

        lines.append(f"Category: {category}")
        lines.append(f"Present: {'YES' if present else 'NO'}")

        if present:
            lines.append(f"Total Amount: {data.get('total_amount', 0):,.2f}")
            lines.append(f"Transaction Count: {data.get('transaction_count', 0)}")

            txns = data.get('supporting_transactions', [])
            if txns:
                lines.append("\nSupporting Transactions:")
                lines.append("-" * 60)
                for i, txn in enumerate(txns, 1):
                    date = txn.get('date', 'N/A')
                    amount = txn.get('amount', 0)
                    narration = txn.get('narration', 'N/A')
                    direction = txn.get('direction', 'N/A')
                    txn_type = txn.get('transaction_type', 'N/A')
                    lines.append(f"{i}. [{date}] {direction} {amount:,.2f} - {narration} ({txn_type})")
                lines.append("-" * 60)
        else:
            lines.append("No matching transactions found for this category.")

        return "\n".join(lines)

    def _format_customer_report(self, data: Dict[str, Any]) -> str:
        """Format customer report result with key highlights."""
        lines = []
        meta = data.get('meta', {})

        lines.append("=" * 60)
        lines.append("CUSTOMER FINANCIAL REPORT")
        lines.append("=" * 60)
        cust_id = meta.get('customer_id', 'N/A')
        lines.append(f"Customer ID: {mask_customer_id(cust_id) if cust_id != 'N/A' else 'N/A'}")
        if meta.get('prty_name'):
            lines.append(f"Customer Name: {meta.get('prty_name')}")
        lines.append(f"Period: {meta.get('analysis_period', 'N/A')}")
        lines.append(f"Transactions Analyzed: {meta.get('transaction_count', 0)}")
        lines.append(f"Report Generated: {meta.get('generated_at', 'N/A')[:10]}")
        lines.append(f"\nReport saved to: {data.get('pdf_path', 'N/A')}")
        lines.append("-" * 60)

        # Populated sections
        sections = data.get('populated_sections', [])
        lines.append(f"Sections included: {', '.join(sections)}")

        # Salary info
        salary = data.get('salary')
        if salary:
            lines.append(f"\nSalary: {salary.get('avg_amount', 0):,.2f} INR ({salary.get('frequency', 0)} transactions)")
            latest = salary.get('latest_transaction')
            if latest:
                lines.append(f"  Latest: {latest.get('amount', 0):,.2f} INR on {latest.get('date', 'N/A')[:10]}")

        # Category overview - top 5
        cat_overview = data.get('category_overview')
        if cat_overview:
            lines.append("\nTop Spending Categories:")
            sorted_cats = sorted(cat_overview.items(), key=lambda x: x[1], reverse=True)[:5]
            for cat, amt in sorted_cats:
                lines.append(f"  - {cat}: {amt:,.2f}")

        # Monthly cashflow summary
        cashflow = data.get('monthly_cashflow')
        if cashflow:
            total_in = sum(m.get('inflow', 0) for m in cashflow)
            total_out = sum(m.get('outflow', 0) for m in cashflow)
            lines.append(f"\nCashflow Summary ({len(cashflow)} months):")
            lines.append(f"  Total Inflow: {total_in:,.0f}")
            lines.append(f"  Total Outflow: {total_out:,.0f}")
            lines.append(f"  Net: {total_in - total_out:,.0f}")

        # EMI
        emis = data.get('emis')
        if emis:
            total_emi = sum(e.get('amount', 0) for e in emis)
            lines.append(f"\nEMI Commitments: {total_emi:,.2f}")

        # Rent
        rent = data.get('rent')
        if rent:
            lines.append(f"Rent: {rent.get('amount', 0):,.2f}")

        # Customer review (LLM summary)
        review = data.get('customer_review')
        if review:
            lines.append(f"\nExecutive Summary:\n{review}")

        lines.append("=" * 60)

        return "\n".join(lines)

    def format_simple(self, results: List[ToolResult]) -> str:
        """Simple formatting without LLM - for faster responses."""
        lines = []
        for r in results:
            if r.success:
                data = r.result
                if "total_spending" in data:
                    lines.append(f"Total spending: ${data['total_spending']:,.2f}")
                    if "transaction_count" in data:
                        lines.append(f"Number of transactions: {data['transaction_count']}")
                    if "month_wise_spending" in data:
                        lines.append("Monthly spending:")
                        for month, amount in data['month_wise_spending'].items():
                            lines.append(f"  {month}: ${amount:,.2f}")
                if "total_income" in data:
                    lines.append(f"Total income: ${data['total_income']:,.2f}")
                    if "transaction_count" in data:
                        lines.append(f"Number of transactions: {data['transaction_count']}")
                if "category_spending" in data:
                    lines.append(f"Spending on {data['category']}: ${data['category_spending']:,.2f}")
                if "all_categories_spending" in data:
                    lines.append("Spending by category:")
                    for cat, amount in data['all_categories_spending'].items():
                        count = data.get('transactions_by_category', {}).get(cat, 0)
                        lines.append(f"  {cat}: ${amount:,.2f} ({count} transactions)")
                if "top_categories" in data:
                    lines.append("Top spending categories:")
                    for i, (cat, amt) in enumerate(data['top_categories'].items(), 1):
                        lines.append(f"  {i}. {cat}: ${amt:,.2f}")
                if "customers" in data:
                    lines.append(f"Customers: {data['customers']}")
                if "categories" in data:
                    lines.append(f"Categories: {data['categories']}")
                # Category presence lookup
                if "present" in data and "category" in data:
                    lines.append(self._format_category_presence(data))
                if "pdf_path" in data and "populated_sections" in data:
                    lines.append(self._format_customer_report(data))
        return "\n".join(lines) if lines else "No results found."
