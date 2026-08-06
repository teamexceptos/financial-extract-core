from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from models.transaction import TransactionListResponse
from services.transaction_service import (
    extract_transactions_from_pdf_bytes,
    extract_transactions_from_text,
    write_transactions_to_csv,
)
from services.receipt import ReceiptExtractionError, extract_receipt_text_from_file_bytes

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.post("/extract", response_model=TransactionListResponse)
async def extract_transactions(
    file: UploadFile = File(...),
    only_bills: bool = Form(False),
):
    return await _run_extraction(file, only_bills)


@router.post("/bills", response_model=TransactionListResponse)
async def extract_bills(file: UploadFile = File(...)):
    return await _run_extraction(file, only_bills=True)


async def _run_extraction(file: UploadFile, only_bills: bool) -> TransactionListResponse:
    file_bytes = await file.read()
    filename = file.filename or "document"
    content_type = file.content_type or ""
    is_pdf = filename.lower().endswith(".pdf") or content_type == "application/pdf" or file_bytes[:4] == b"%PDF"

    try:
        if is_pdf:
            transactions = extract_transactions_from_pdf_bytes(
                file_bytes, filename=filename, only_bills=only_bills,
            )
        else:
            extracted_text, source, _ = extract_receipt_text_from_file_bytes(
                file_bytes, filename=filename, content_type=content_type,
            )
            transactions = extract_transactions_from_text(
                extracted_text, source_name=source, only_bills=only_bills,
            )
    except ReceiptExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

    csv_path = write_transactions_to_csv(transactions, f"/tmp/{filename}.csv")
    source = transactions[0].source if transactions else filename

    return TransactionListResponse(
        source=source,
        total=len(transactions),
        only_bills=only_bills,
        transactions=transactions,
        csv_path=str(csv_path),
    )
