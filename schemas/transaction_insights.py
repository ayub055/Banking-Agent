"""Transaction insight schemas for pattern extraction."""

from typing import List, Optional
from pydantic import BaseModel, Field


class TransactionPattern(BaseModel):
    """A single descriptive pattern found in transactions."""
    pattern: str = Field(
        description="Pattern type: subscription-heavy, salary-consistent, rent-recurring, etc."
    )
    evidence: List[str] = Field(
        default_factory=list,
        description="Categories or transaction types supporting this pattern"
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in pattern detection"
    )


class TransactionInsights(BaseModel):
    """Aggregated transaction insights for a customer."""
    customer_id: int
    scope: str = Field(
        default="patterns",
        description="Analysis scope used: patterns, recurring_only, top_merchants, credits_only"
    )
    patterns: List[TransactionPattern] = Field(default_factory=list)
    transaction_count_analyzed: int = Field(default=0)

    def to_explainer_context(self) -> str:
        """Convert insights to a string for explainer context."""
        if not self.patterns:
            return ""

        lines = ["Transaction Patterns Detected:"]
        for p in self.patterns:
            evidence_str = ", ".join(p.evidence[:3])
            lines.append(f"  - {p.pattern}: {evidence_str}")
        return "\n".join(lines)
