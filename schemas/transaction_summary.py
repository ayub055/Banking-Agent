"""Transaction summary schemas for deterministic fetching and aggregation."""

from typing import List, Optional
from pydantic import BaseModel, Field


class SalarySummary(BaseModel):
    """Summary of detected salary/income transactions."""
    average_amount: float = Field(description="Average salary amount")
    frequency: str = Field(default="monthly", description="Payment frequency")
    narrations: List[str] = Field(default_factory=list, description="Original narrations")
    transaction_count: int = Field(default=0, description="Number of salary transactions")
    total_amount: float = Field(default=0.0, description="Total salary received")


class HighFrequencyTransaction(BaseModel):
    """A group of similar transactions identified via fuzzy matching."""
    representative_narration: str = Field(description="Primary narration for the group")
    similar_narrations: List[str] = Field(default_factory=list, description="All similar narrations")
    count: int = Field(description="Number of transactions in group")
    total_amount: float = Field(description="Total amount for this group")
    average_amount: float = Field(default=0.0, description="Average transaction amount")
    transaction_type: str = Field(default="DR", description="DR (debit) or CR (credit)")
    score: float = Field(default=0.0, description="Hybrid ranking score (frequency x amount)")


class TransactionSummary(BaseModel):
    """Complete transaction summary for a customer."""
    customer_id: int
    salary_summary: Optional[SalarySummary] = None
    high_frequency_transactions: List[HighFrequencyTransaction] = Field(default_factory=list)
    total_transactions_analyzed: int = Field(default=0)

    def to_explainer_context(self) -> str:
        """Convert summary to string for LLM explainer context."""
        lines = []

        if self.salary_summary:
            lines.append("Salary/Income Summary:")
            lines.append(f"  - Average: {self.salary_summary.average_amount:,.2f} INR")
            lines.append(f"  - Frequency: {self.salary_summary.frequency}")
            lines.append(f"  - Count: {self.salary_summary.transaction_count} transactions")

        if self.high_frequency_transactions:
            lines.append("\nHigh-Frequency Transactions:")
            for txn in self.high_frequency_transactions[:5]:  # Limit to top 5
                lines.append(f"  - {txn.representative_narration}: {txn.count}x, {txn.total_amount:,.2f} INR")

        return "\n".join(lines) if lines else ""
