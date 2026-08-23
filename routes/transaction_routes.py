"""Transaction extraction API routes.

Exposes ``POST /transactions/extract`` and ``POST /transactions/bills``
which accept a file upload, optional ``bank`` hint, and return parsed
transaction rows along with statement summary metadata.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from models.transaction import TransactionListResponse
from services.banks.ng import detect_bank_type, extract_statement_summary
from services.receipt import ReceiptExtractionError, extract_receipt_text_from_file_bytes
from services.transaction_service import (
    TransactionExtractionError,
    extract_transactions_from_text,
    write_transactions_to_csv,
)

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.post("/extract", response_model=TransactionListResponse)
async def extract_transactions(
    file: UploadFile = File(...),
    bank: str | None = Form(None),
    only_bills: bool = Form(False),
):
    """Extract transactions and account summary from bank statement file.

    - **file**: PDF, image, or text statement file.
    - **bank**: Optional bank code hint (``opay``, ``gtb``, ``moniepoint``, etc.).
    - **only_bills**: When ``true``, only utility / bill transactions are returned.
    """
    return await _run_extraction(file, bank=bank, only_bills=only_bills)


@router.post("/bills", response_model=TransactionListResponse)
async def extract_bills(
    file: UploadFile = File(...),
    bank: str | None = Form(None),
):
    """Extract only bill/utility transactions from bank statement file."""
    return await _run_extraction(file, bank=bank, only_bills=True)


async def _run_extraction(
    file: UploadFile,
    *,
    bank: str | None = None,
    only_bills: bool = False,
) -> TransactionListResponse:
    file_bytes = await file.read()
    filename = file.filename or "document"
    content_type = file.content_type or ""

    try:
        extracted_text, source, meta = extract_receipt_text_from_file_bytes(
            file_bytes,
            filename=filename,
            content_type=content_type,
            actual_file=file,
        )
        _, transactions = extract_transactions_from_text(
            extracted_text,
            bank=bank,
            source_name=source,
            only_bills=only_bills,
        )
    except (ReceiptExtractionError, TransactionExtractionError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

    confidence: float | None = meta.get("confidence") if isinstance(meta, dict) else None
    detected_bank: str | None = bank or detect_bank_type(extracted_text)
    summary = extract_statement_summary(extracted_text, bank=bank)

    csv_path: str | None = None
    try:
        csv_path = str(write_transactions_to_csv(transactions, f"/tmp/{filename}.csv"))
    except Exception:
        csv_path = None

    source_label = transactions[0].source if transactions else filename

    return TransactionListResponse(
        raw_text=extracted_text,
        source=source_label,
        total=len(transactions),
        only_bills=only_bills,
        detected_bank=detected_bank,
        confidence=confidence,
        summary=summary,
        transactions=transactions,
        csv_path=csv_path,
    )
