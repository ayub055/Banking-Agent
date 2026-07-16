"""Customer report schema - single source of truth for report state.

This module defines the canonical report object that flows through the
report generation pipeline. All sections are optional to support
conditional rendering based on data availability.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class ReportMeta(BaseModel):
    """Report metadata."""
    customer_id: int
    prty_name: Optional[str] = Field(default=None, description="Party/Customer name")
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    analysis_period: str = Field(default="Last 6 months")
    currency: str = Field(default="INR")
    transaction_count: int = Field(default=0)


class SalaryBlock(BaseModel):
    """Salary/income summary block."""
    avg_amount: float
    frequency: int = Field(description="Number of salary transactions")
    narration: str = Field(default="", description="Representative narration")
    sample_transaction: Dict[str, Any] = Field(default_factory=dict)
    latest_transaction: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Latest month's salary transaction with date and amount"
    )
    dates: List[str] = Field(default_factory=list, description="All occurrence dates (YYYY-MM-DD)")


class EMIBlock(BaseModel):
    """EMI payment block."""
    name: str = Field(default="EMI Payment")
    amount: float = Field(description="Average EMI amount")
    frequency: int = Field(description="Number of EMI transactions")
    sample_transaction: Dict[str, Any] = Field(default_factory=dict)
    dates: List[str] = Field(default_factory=list, description="All occurrence dates (YYYY-MM-DD)")


class BillBlock(BaseModel):
    """Utility/bill payment block."""
    bill_type: str
    frequency: int
    avg_amount: float
    sample_transaction: Dict[str, Any] = Field(default_factory=dict)
    dates: List[str] = Field(default_factory=list, description="All occurrence dates (YYYY-MM-DD)")


class RentBlock(BaseModel):
    """Rent payment block."""
    direction: str = Field(default="paid", description="paid or received")
    frequency: int
    amount: float = Field(description="Average rent amount")
    sample_transaction: Dict[str, Any] = Field(default_factory=dict)
    dates: List[str] = Field(default_factory=list, description="All occurrence dates (YYYY-MM-DD)")


class CustomerReport(BaseModel):
    """
    Canonical customer report object.

    All section fields are Optional - they will only be populated
    if the corresponding data exists for the customer. The template
    uses conditional rendering to omit empty sections.
    """
    meta: ReportMeta

    # Section 3 - Category and cashflow data
    category_overview: Optional[Dict[str, float]] = Field(
        default=None,
        description="Spending by category"
    )
    monthly_cashflow: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Monthly inflow/outflow/net"
    )
    top_merchants: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Top merchants by transaction frequency"
    )

    # Section 4 - Presence-based blocks (only if detected)
    salary: Optional[SalaryBlock] = None
    emis: Optional[List[EMIBlock]] = None
    bills: Optional[List[BillBlock]] = None
    rent: Optional[RentBlock] = None

    # Section 2 - LLM-generated summary (optional)
    customer_review: Optional[str] = Field(
        default=None,
        description="LLM-generated executive summary"
    )

    # Account quality (primary / conduit / secondary classification)
    account_quality: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Account quality analysis — primary/conduit/secondary classification with conduit events"
    )

    # Detected transaction events (PF withdrawal, post-salary routing, loan redistribution, etc.)
    events: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Semantic events detected from raw narrations — fed to LLM for intelligent summary"
    )

    # Merchant-level behavioral features
    merchant_features: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Merchant-level behavioral features for credit assessment"
    )

    # Deterministic review checklist ({"banking": [ {label, checked, severity, detail}, ... ]})
    checklist: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Deterministic risk/FCU/fraud checklist, computed at build time"
    )

    def has_any_presence_block(self) -> bool:
        """Check if any presence-based block is populated."""
        return any([self.salary, self.emis, self.bills, self.rent])

    def get_populated_sections(self) -> List[str]:
        """Return list of populated section names for debugging."""
        sections = []
        if self.category_overview:
            sections.append("category_overview")
        if self.monthly_cashflow:
            sections.append("monthly_cashflow")
        if self.top_merchants:
            sections.append("top_merchants")
        if self.salary:
            sections.append("salary")
        if self.emis:
            sections.append("emis")
        if self.bills:
            sections.append("bills")
        if self.rent:
            sections.append("rent")
        if self.account_quality:
            sections.append("account_quality")
        if self.events:
            sections.append("events")
        if self.merchant_features:
            sections.append("merchant_features")
        if self.customer_review:
            sections.append("customer_review")
        return sections
