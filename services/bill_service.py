from __future__ import annotations

"""
Backwards compatibility module for bill_service.
Renamed to services.transaction_service.
"""

from services.transaction_service import (
    export_bills_from_data_dir,
    export_transactions_from_data_dir,
    extract_bills_from_data_dir,
    extract_bills_from_file,
    extract_bills_from_text,
    extract_transactions_from_data_dir,
    extract_transactions_from_file,
    extract_transactions_from_pdf_bytes,
    extract_transactions_from_text,
    write_bills_to_csv,
    write_transactions_to_csv,
)

__all__ = [
    "export_bills_from_data_dir",
    "export_transactions_from_data_dir",
    "extract_bills_from_data_dir",
    "extract_bills_from_file",
    "extract_bills_from_text",
    "extract_transactions_from_data_dir",
    "extract_transactions_from_file",
    "extract_transactions_from_pdf_bytes",
    "extract_transactions_from_text",
    "write_bills_to_csv",
    "write_transactions_to_csv",
]
