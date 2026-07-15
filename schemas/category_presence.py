"""Category presence lookup result schema."""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class SupportingTransaction(BaseModel):
    """A transaction supporting category presence."""
    date: str
    amount: float
    narration: str
    transaction_type: str  # UPI, IMPS, etc.
    direction: str  # DR or CR


class CategoryPresenceResult(BaseModel):
    """Result of category presence lookup."""
    customer_id: int
    category: str
    present: bool
    total_amount: float = Field(default=0.0)
    transaction_count: int = Field(default=0)
    supporting_transactions: List[SupportingTransaction] = Field(default_factory=list)

    # Additional metadata for debugging/audit
    direction_filter: Optional[str] = Field(
        default=None,
        description="DR, CR, or None for both"
    )
    matched_keywords: List[str] = Field(default_factory=list)
    category_config_used: str = Field(
        default="",
        description="Category key from YAML config that was used"
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for tool result output."""
        return {
            "category": self.category,
            "present": self.present,
            "total_amount": self.total_amount,
            "transaction_count": self.transaction_count,
            "supporting_transactions": [
                {
                    "date": t.date,
                    "amount": t.amount,
                    "narration": t.narration,
                    "transaction_type": t.transaction_type,
                    "direction": t.direction
                }
                for t in self.supporting_transactions
            ]
        }
