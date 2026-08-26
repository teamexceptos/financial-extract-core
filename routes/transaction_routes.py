"""Transaction extraction API routes.

Exposes ``POST /transactions/extract`` and ``POST /transactions/bills``
which accept a file upload, optional ``bank`` hint, optional ``extractor``
backend selection, and return parsed transaction rows along with statement
summary metadata.

Extractor backends
------------------
``pdf_inspector`` (default)
    Uses pdf-inspector to parse PDF structure and emit markdown, with an
    automatic fallback to pdfplumber geometry reconstruction when the markdown
    tables are corrupted by pipe characters in cell values.

``pdfplumber``
    Uses pdfplumber's geometry engine directly — recovers column boundaries
    from the page layout rather than from markdown. Preferred for statements
    where column position carries the meaning (Kuda, Moniepoint wide exports).
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from models.transaction import TransactionListResponse
from services.banks.ng import detect_bank_type, extract_statement_summary
from services.receipt import (
    ReceiptExtractionError,
    extract_receipt_text_from_file_bytes,
)
from utils.pdf_layout import PDF_BACKEND_PDF_INSPECTOR, PDF_BACKEND_PDFPLUMBER
from services.transaction_service import (
    TransactionExtractionError,
    extract_transactions_from_text,
    write_transactions_to_csv,
)

router = APIRouter(prefix="/transactions", tags=["Transactions"])

_EXTRACTOR_CHOICES = {PDF_BACKEND_PDF_INSPECTOR, PDF_BACKEND_PDFPLUMBER}


@router.post("/extract", response_model=TransactionListResponse)
async def extract_transactions(
    file: UploadFile = File(...),
    bank: str | None = Form(None),
    only_bills: bool = Form(False),
    extractor: str = Form(PDF_BACKEND_PDF_INSPECTOR),
):

    return await _run_extraction(file, bank=bank, only_bills=only_bills, extractor=extractor)


@router.post("/bills", response_model=TransactionListResponse)
async def extract_bills(
    file: UploadFile = File(...),
    bank: str | None = Form(None),
    extractor: str = Form(PDF_BACKEND_PDF_INSPECTOR),
):
    """Extract only bill/utility transactions from a bank statement file.

    - **extractor**: PDF extraction backend — ``pdf_inspector`` (default) or ``pdfplumber``.
    """
    return await _run_extraction(file, bank=bank, only_bills=True, extractor=extractor)


async def _run_extraction(
    file: UploadFile,
    *,
    bank: str | None = None,
    only_bills: bool = False,
    extractor: str = PDF_BACKEND_PDF_INSPECTOR,
) -> TransactionListResponse:
    # Validate backend before reading the file.
    if extractor not in _EXTRACTOR_CHOICES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid extractor '{extractor}'. Choose from: {sorted(_EXTRACTOR_CHOICES)}",
        )

    file_bytes = await file.read()
    filename = file.filename or "document"
    content_type = file.content_type or ""

    try:
        extracted_text, source, meta = extract_receipt_text_from_file_bytes(
            file_bytes,
            filename=filename,
            content_type=content_type,
            actual_file=file,
            backend=extractor,
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
