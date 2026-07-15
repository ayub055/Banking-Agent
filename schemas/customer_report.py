"""Customer report schema - single source of truth for report state.

This module defines the canonical report object that flows through the
report generation pipeline. All sections are optional to support
conditional rendering based on data availability.
"""

from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator
from datetime import datetime


# Valid values for constrained fields
VALID_RISK_LEVELS = ("low", "medium", "high", "unknown")
VALID_BALANCE_TRENDS = ("increasing", "decreasing", "stable", "no_data", "insufficient_data", "unknown")


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


class SavingsBlock(BaseModel):
    """Savings analysis block - derived from income vs spending."""
    total_income: float = Field(description="Total credits/income")
    total_spending: float = Field(description="Total debits/spending")
    net_savings: float = Field(description="Income minus spending")
    savings_rate: float = Field(description="Savings as percentage of income (0-1)")
    avg_monthly_savings: float = Field(default=0, description="Average monthly net savings")
    months_analyzed: int = Field(default=0, description="Number of months in analysis")


class RiskIndicatorsBlock(BaseModel):
    """Risk indicators block - flags potential financial risks."""
    income_stability_score: float = Field(description="Income stability (0-100, higher=more stable)")
    balance_trend: str = Field(description="increasing/decreasing/stable")
    credit_spike_count: int = Field(default=0, description="Number of unusual credit transactions")
    debit_spike_count: int = Field(default=0, description="Number of unusual spending transactions")
    risk_flags: List[str] = Field(default_factory=list, description="List of identified risk factors")
    risk_level: str = Field(default="unknown", description="low/medium/high risk assessment")

    @field_validator('balance_trend')
    @classmethod
    def validate_balance_trend(cls, v: str) -> str:
        if v not in VALID_BALANCE_TRENDS:
            return "unknown"
        return v

    @field_validator('risk_level')
    @classmethod
    def validate_risk_level(cls, v: str) -> str:
        if v not in VALID_RISK_LEVELS:
            return "unknown"
        return v

    @field_validator('income_stability_score')
    @classmethod
    def validate_stability_score(cls, v: float) -> float:
        return max(0.0, min(100.0, v))  # Clamp to 0-100


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

    # Section 5 - Derived analysis blocks
    savings: Optional[SavingsBlock] = Field(
        default=None,
        description="Savings analysis derived from income vs spending"
    )
    risk_indicators: Optional[RiskIndicatorsBlock] = Field(
        default=None,
        description="Risk assessment indicators"
    )

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
        if self.savings:
            sections.append("savings")
        if self.risk_indicators:
            sections.append("risk_indicators")
        if self.account_quality:
            sections.append("account_quality")
        if self.events:
            sections.append("events")
        if self.merchant_features:
            sections.append("merchant_features")
        if self.customer_review:
            sections.append("customer_review")
        return sections
