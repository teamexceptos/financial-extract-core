"""Transaction extraction and processing service.

Orchestrates bank statement transaction extraction through:
  1. Ingestion: PDF (via pdf-inspector + OCR fallback), plain text, or image OCR.
  2. Routing: Dispatches to per-bank extractors in ``services.banks.ng``.
  3. Summaries: Extracts statement summary metadata (opening/closing balance, totals, account name, etc.).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from models.transaction import TransactionRecord
from services.banks.ng import (
    detect_bank_type,
    extract_statement_summary,
    get_bank_extractor,
)


class TransactionExtractionError(RuntimeError):
    """Raised when transaction extraction fails."""


def _normalize_text(text: str) -> str:
    text = text or ""
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def extract_transaction_text_from_file_bytes(
    file_bytes: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
    actual_file: Any | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Extract raw text from uploaded file bytes."""
    from services.receipt import extract_receipt_text_from_file_bytes

    return extract_receipt_text_from_file_bytes(
        file_bytes,
        filename=filename,
        content_type=content_type,
        actual_file=actual_file,
    )


def extract_transactions_from_text(
    text: str,
    *,
    bank: str | None = None,
    source_name: str | None = None,
    only_bills: bool = False,
) -> tuple[str, list[TransactionRecord]]:
    """Extract transactions from text using the resolved bank extractor."""
    normalized = _normalize_text(text)
    if not normalized:
        return "", []

    source = source_name or "extracted_text"
    extractor = get_bank_extractor(bank_name=bank, text=normalized)
    return extractor.extract(normalized, source_name=source, only_bills=only_bills)


def extract_transactions_from_file(
    file_path: str | Path,
    *,
    bank: str | None = None,
    source_name: str | None = None,
    only_bills: bool = False,
) -> tuple[str, list[TransactionRecord]]:
    """Read a local file (PDF or text) and extract transactions."""
    path = Path(file_path)
    if not path.exists():
        return "", []

    if path.suffix.lower() == ".pdf":
        return extract_transactions_from_pdf_bytes(
            path.read_bytes(),
            bank=bank,
            filename=path.name,
            source_name=source_name or path.stem,
            only_bills=only_bills,
        )
    return extract_transactions_from_text(
        path.read_text(encoding="utf-8", errors="ignore"),
        bank=bank,
        source_name=source_name or path.stem,
        only_bills=only_bills,
    )


def extract_transactions_from_pdf_bytes(
    pdf_bytes: bytes,
    *,
    bank: str | None = None,
    filename: str | None = None,
    source_name: str | None = None,
    only_bills: bool = False,
    actual_file: Any | None = None,
) -> tuple[str, list[TransactionRecord]]:
    """Extract text from PDF bytes then parse transactions."""
    text, src, _meta = extract_transaction_text_from_file_bytes(
        pdf_bytes,
        filename=filename or "document.pdf",
        content_type="application/pdf",
        actual_file=actual_file,
    )
    return extract_transactions_from_text(
        text,
        bank=bank,
        source_name=source_name or (Path(filename).stem if filename else src),
        only_bills=only_bills,
    )


def extract_transactions_from_data_dir(
    data_dir: str | Path,
    *,
    bank: str | None = None,
    only_bills: bool = False,
) -> list[TransactionRecord]:
    """Batch-extract transactions from all .txt and .pdf files in a directory."""
    directory = Path(data_dir)
    if not directory.exists():
        return []
    all_txs: list[TransactionRecord] = []
    for path in sorted(list(directory.glob("*.txt")) + list(directory.glob("*.pdf"))):
        _, txs = extract_transactions_from_file(path, bank=bank, only_bills=only_bills)
        all_txs.extend(txs)
    return all_txs


def write_transactions_to_csv(
    transactions: list[TransactionRecord],
    output_path: str | Path,
) -> Path:
    """Write transaction records to a CSV file."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "source", "description", "transaction_number", "amount",
        "debit", "credit", "balance", "transaction_type", "category",
        "date", "time", "days_from_today", "is_within_3_months",
    ]
    with destination.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for tx in transactions:
            m = tx.metadata
            writer.writerow({
                "source": tx.source,
                "description": tx.description,
                "transaction_number": m.transaction_number or "",
                "amount": m.amount or "",
                "debit": m.debit or "",
                "credit": m.credit or "",
                "balance": m.balance or "",
                "transaction_type": m.transaction_type or "",
                "category": m.category or "",
                "date": m.date or "",
                "time": m.time or "",
                "days_from_today": m.days_from_today if m.days_from_today is not None else "",
                "is_within_3_months": m.is_within_3_months if m.is_within_3_months is not None else "",
            })

    return destination


extract_bills_from_text = extract_transactions_from_text
extract_bills_from_file = extract_transactions_from_file
extract_bills_from_data_dir = extract_transactions_from_data_dir
write_bills_to_csv = write_transactions_to_csv

__all__ = [
    "TransactionExtractionError",
    "detect_bank_type",
    "extract_statement_summary",
    "extract_transaction_text_from_file_bytes",
    "extract_transactions_from_data_dir",
    "extract_transactions_from_file",
    "extract_transactions_from_pdf_bytes",
    "extract_transactions_from_text",
    "get_bank_extractor",
    "write_transactions_to_csv",
]
