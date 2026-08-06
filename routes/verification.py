from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, HTTPException

from models.transaction import (
    AmountThresholdRequest,
    CategorySummaryItem,
    CategorySummaryResult,
    DateRangeFilterRequest,
    DuplicateCheckRequest,
    DuplicateCheckResult,
    DuplicateGroup,
    TransactionListResponse,
    TransactionRecord,
)
from models.verification import (
    AddressInput,
    AddressVerificationResult,
    GoogleLatLngVerifyResult,
    ReceiptCrossRefRequest,
    ReceiptCrossRefResult,
)
from services.address import verify_address_input_service
from services.google import google_verify_address_from_lat_lng_service
from services.receipt import cross_reference_receipt_address_service

router = APIRouter(prefix="/verification", tags=["Verification"])


@router.post("/address", response_model=AddressVerificationResult)
def verify_address(address: AddressInput):
    return verify_address_input_service(address)


@router.post("/address-crossref", response_model=ReceiptCrossRefResult)
def crossref_address_with_receipt(req: ReceiptCrossRefRequest):
    result = verify_address_input_service(req.address)
    if not result.is_structurally_valid:
        raise HTTPException(status_code=422, detail={"issues": result.issues})
    return cross_reference_receipt_address_service(req.address, req.receipt_text)


@router.get("/address-reverse", response_model=GoogleLatLngVerifyResult)
def reverse_geocode(lat: float, lng: float):
    return google_verify_address_from_lat_lng_service(lat, lng)


@router.post("/transactions/duplicates", response_model=DuplicateCheckResult)
def check_duplicates(req: DuplicateCheckRequest):
    """
    Detect duplicate transactions by matching on (description, amount, date).
    Returns each group of duplicates and the indices of the matching records.
    """
    seen: dict[tuple, list[int]] = defaultdict(list)
    for i, tx in enumerate(req.transactions):
        key = (
            tx.description.strip().lower(),
            tx.metadata.amount,
            tx.metadata.date,
        )
        seen[key].append(i)

    groups: list[DuplicateGroup] = []
    for (desc, amount, txdate), indices in seen.items():
        if len(indices) > 1:
            groups.append(DuplicateGroup(
                description=desc,
                amount=amount,
                date=txdate,
                count=len(indices),
                indices=indices,
            ))

    return DuplicateCheckResult(
        total_checked=len(req.transactions),
        duplicate_groups=groups,
        has_duplicates=len(groups) > 0,
    )


@router.post("/transactions/filter-by-date", response_model=TransactionListResponse)
def filter_by_date_range(req: DateRangeFilterRequest):
    """
    Filter a list of transactions to those whose date falls within [from_date, to_date].
    Transactions with no date are excluded.
    """
    try:
        from_dt = date.fromisoformat(req.from_date)
        to_dt = date.fromisoformat(req.to_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must be ISO-8601 format: YYYY-MM-DD")

    if from_dt > to_dt:
        raise HTTPException(status_code=422, detail="from_date must be on or before to_date")

    filtered: list[TransactionRecord] = []
    for tx in req.transactions:
        tx_date_str = tx.metadata.date
        if not tx_date_str:
            continue
        try:
            tx_date = date.fromisoformat(tx_date_str)
        except ValueError:
            continue
        if from_dt <= tx_date <= to_dt:
            filtered.append(tx)

    source = filtered[0].source if filtered else (req.transactions[0].source if req.transactions else "unknown")
    return TransactionListResponse(source=source, total=len(filtered), transactions=filtered)


@router.post("/transactions/filter-by-amount", response_model=TransactionListResponse)
def filter_by_amount(req: AmountThresholdRequest):
    """
    Filter transactions by amount range and/or transaction type (debit/credit).
    All filters are optional and combinable.
    """
    if req.min_amount is not None and req.max_amount is not None and req.min_amount > req.max_amount:
        raise HTTPException(status_code=422, detail="min_amount must be <= max_amount")

    filtered: list[TransactionRecord] = []
    for tx in req.transactions:
        if req.transaction_type and tx.metadata.transaction_type != req.transaction_type:
            continue
        amount_str = tx.metadata.amount
        if not amount_str:
            continue
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        if req.min_amount is not None and amount < req.min_amount:
            continue
        if req.max_amount is not None and amount > req.max_amount:
            continue
        filtered.append(tx)

    source = filtered[0].source if filtered else (req.transactions[0].source if req.transactions else "unknown")
    return TransactionListResponse(source=source, total=len(filtered), transactions=filtered)


@router.post("/transactions/category-summary", response_model=CategorySummaryResult)
def category_summary(transactions: list[TransactionRecord]):
    """
    Summarise transactions by category — total count, total debits, and total credits per category.
    Transactions without a category are grouped under 'Uncategorised'.
    """
    buckets: dict[str, dict] = defaultdict(lambda: {"count": 0, "debit": 0.0, "credit": 0.0})

    for tx in transactions:
        cat = tx.metadata.category or "Uncategorised"
        buckets[cat]["count"] += 1
        if tx.metadata.debit:
            try:
                buckets[cat]["debit"] += float(tx.metadata.debit)
            except ValueError:
                pass
        if tx.metadata.credit:
            try:
                buckets[cat]["credit"] += float(tx.metadata.credit)
            except ValueError:
                pass

    categories = [
        CategorySummaryItem(
            category=cat,
            count=data["count"],
            total_debit=round(data["debit"], 2),
            total_credit=round(data["credit"], 2),
        )
        for cat, data in sorted(buckets.items(), key=lambda x: -x[1]["count"])
    ]

    return CategorySummaryResult(total_transactions=len(transactions), categories=categories)


@router.post("/transactions/validate", response_model=dict)
def validate_transactions(transactions: list[TransactionRecord]):
    """
    Run a suite of validation checks on a list of transactions and return a report.

    Checks performed:
    - Missing date
    - Missing amount
    - Missing transaction_type
    - Transactions with both debit and credit set (ambiguous)
    - Future-dated transactions (date > today)
    - Large transactions (> 5,000,000)
    """
    today = date.today()
    issues: list[dict] = []

    for i, tx in enumerate(transactions):
        m = tx.metadata
        flags: list[str] = []

        if not m.date:
            flags.append("missing_date")
        else:
            try:
                tx_date = date.fromisoformat(m.date)
                if tx_date > today:
                    flags.append("future_dated")
            except ValueError:
                flags.append("invalid_date_format")

        if not m.amount:
            flags.append("missing_amount")
        else:
            try:
                val = float(m.amount)
                if val > 5_000_000:
                    flags.append("large_transaction")
            except ValueError:
                flags.append("invalid_amount_format")

        if not m.transaction_type:
            flags.append("missing_transaction_type")

        if m.debit and m.credit:
            flags.append("ambiguous_debit_and_credit_both_set")

        if flags:
            issues.append({"index": i, "description": tx.description[:80], "flags": flags})

    return {
        "total": len(transactions),
        "valid": len(transactions) - len(issues),
        "invalid": len(issues),
        "issues": issues,
    }
