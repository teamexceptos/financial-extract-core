from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TransactionMetadata(BaseModel):
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    transaction_type: Literal["debit", "credit"] | None = None
    date: str | None = None
    days_from_today: int | None = None
    is_within_3_months: bool | None = None
    category: str | None = None
    transaction_number: str | None = None


class TransactionRecord(BaseModel):
    source: str
    description: str
    metadata: TransactionMetadata


class ReceiptDetail(BaseModel):
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    transaction_type: Literal["debit", "credit"] | None = None
    date: str | None = None
    days_from_today: int | None = None
    is_within_3_months: bool | None = None
    category: str | None = None
    address: str | None = None
    receipt_number: str | None = None
    receipt_type: str | None = None


class ReceiptRecord(BaseModel):
    source: str
    description: str
    metadata: ReceiptDetail


class TransactionListResponse(BaseModel):
    source: str
    total: int = Field(ge=0)
    only_bills: bool = False
    transactions: list[TransactionRecord] = Field(default_factory=list)
    csv_path: str | None = None


class DuplicateCheckRequest(BaseModel):
    transactions: list[TransactionRecord]


class DuplicateGroup(BaseModel):
    description: str
    amount: str | None
    date: str | None
    count: int
    indices: list[int]


class DuplicateCheckResult(BaseModel):
    total_checked: int
    duplicate_groups: list[DuplicateGroup]
    has_duplicates: bool


class DateRangeFilterRequest(BaseModel):
    transactions: list[TransactionRecord]
    from_date: str = Field(description="ISO-8601 date, e.g. 2025-01-01")
    to_date: str = Field(description="ISO-8601 date, e.g. 2025-12-31")


class AmountThresholdRequest(BaseModel):
    transactions: list[TransactionRecord]
    min_amount: float | None = None
    max_amount: float | None = None
    transaction_type: Literal["debit", "credit"] | None = None


class CategorySummaryItem(BaseModel):
    category: str
    count: int
    total_debit: float
    total_credit: float


class CategorySummaryResult(BaseModel):
    total_transactions: int
    categories: list[CategorySummaryItem]
