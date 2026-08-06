from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from models.transaction import TransactionMetadata, TransactionRecord
from utils.receipt_metadata import categorise_transaction, classify_transaction_type, extract_receipt_metadata_from_text

BILL_CATEGORIES = {"Utility", "Cable TV", "Internet", "Cooking Gas", "Water"}


def _normalize_text(text: str) -> str:
    text = text or ""
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _clean_number(val: str) -> str | None:
    if not val:
        return None
    cleaned = val.strip().replace(",", "")
    if cleaned.startswith(("+", "-")):
        cleaned = cleaned[1:].strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1].strip()
    if re.fullmatch(r"\d*(?:\.\d{1,2})?", cleaned) and cleaned not in ("", "."):
        try:
            return f"{float(cleaned):.2f}"
        except Exception:
            return None
    return None


def _looks_like_transaction_line(line: str) -> bool:
    if not line or len(line.strip()) < 8:
        return False
    lowered = line.lower()
    skip_tokens = [
        "opening balance", "closing balance", "usable balance", "total debit", "total credit",
        "account summary", "statement period", "download app", "privacy policy",
        "customer statement", "print. date", "branch name", "account no", "internal reference",
    ]
    if any(t in lowered for t in skip_tokens):
        return False
    if line.strip().startswith("|") and ("---" in line or "trans date" in lowered or "value date" in lowered or "trans." in lowered):
        return False
    if re.search(r"\d{2}-[a-z]{3}-\s*\d{4}", lowered):
        return True
    return bool(re.search(r"\d[\d,]*(?:\.\d{2})?", line)) and any(
        kw in lowered for kw in ["transfer", "payment", "topup", "charge", "debit", "credit", "sms", "nibss", "ussd", "purchase", "vat", "stamp duty"]
    )


def _parse_transaction_row(line: str) -> tuple[str, dict[str, Any]] | None:
    if not line.startswith("|"):
        return None

    lowered = line.lower()
    skip_tokens = [
        "trans date", "value date", "trans.", "total debit", "total credit",
        "opening balance", "closing balance", "account summary", "statement period", "print. date",
    ]
    if any(t in lowered for t in skip_tokens):
        return None
    if re.match(r"^\|[\s\-|:]+\|$", line.strip()):
        return None

    columns = [col.strip() for col in line.split("|")[1:-1]]
    if len(columns) < 4:
        return None

    # Use the narration column as the description, not all columns joined
    narration = _extract_narration(columns)
    metadata = extract_receipt_metadata_from_text(narration)

    date_value: str | None = None
    if columns and re.search(r"\d{2}-[a-z]{3}-\s*\d{4}", columns[0], re.IGNORECASE):
        try:
            from dateutil import parser as date_parser
            date_value = date_parser.parse(columns[0], fuzzy=True, dayfirst=True).date().isoformat()
        except Exception:
            pass

    debit_val, credit_val = _resolve_debit_credit(columns, narration)
    amount_val: str | None = None
    ttype: str | None = None

    if debit_val and credit_val:
        ttype_kw, _, _ = classify_transaction_type(narration)
        if ttype_kw == "credit":
            debit_val, amount_val, ttype = None, credit_val, "credit"
        else:
            credit_val, amount_val, ttype = None, debit_val, "debit"
    elif debit_val:
        ttype, amount_val = "debit", debit_val
    elif credit_val:
        ttype, amount_val = "credit", credit_val
    else:
        candidates = [_clean_number(col) for i, col in enumerate(columns) if i > 1 and _clean_number(col)]
        if candidates:
            amount_val = candidates[0]
        ttype, debit_fallback, credit_fallback = classify_transaction_type(narration, amount_val)
        if ttype == "debit":
            debit_val = amount_val
        elif ttype == "credit":
            credit_val = amount_val

    if date_value:
        metadata["date"] = date_value
    metadata["amount"] = amount_val
    metadata["debit"] = debit_val
    metadata["credit"] = credit_val
    metadata["transaction_type"] = ttype
    metadata["category"] = categorise_transaction(narration, metadata.get("receipt_type"))

    return narration, metadata


def _extract_narration(columns: list[str]) -> str:
    is_gtb = len(columns) >= 6 and (columns[2].startswith("'") or any(ref in columns[2] for ref in ["GTW", "BR", "API", "NIP", "USS"]))
    if is_gtb and len(columns) >= 8:
        # GTB: date | value_date | ref_code | debit | credit | balance | customer | narration
        narration = columns[7].strip()
        if narration:
            return narration
    if len(columns) >= 3 and columns[2].strip():
        return columns[2].strip()
    return " | ".join(c for c in columns if c.strip())


def _resolve_debit_credit(columns: list[str], narration: str) -> tuple[str | None, str | None]:
    is_gtb = len(columns) >= 6 and (columns[2].startswith("'") or any(ref in columns[2] for ref in ["GTW", "BR", "API", "NIP", "USS"]))
    if is_gtb and len(columns) >= 5:
        # GTB: debit=col[3], credit=col[4]
        return _clean_number(columns[3]), _clean_number(columns[4])
    if len(columns) == 7:
        return _clean_number(columns[4]), _clean_number(columns[5])
    if len(columns) >= 8:
        return _clean_number(columns[3]), _clean_number(columns[4])
    return None, None


def _is_bill_transaction(tx: TransactionRecord) -> bool:
    return tx.metadata.category in BILL_CATEGORIES


def _build_record(description: str, metadata: dict[str, Any], source: str) -> TransactionRecord:
    return TransactionRecord(
        source=source,
        description=description[:240],
        metadata=TransactionMetadata(
            amount=metadata.get("amount"),
            debit=metadata.get("debit"),
            credit=metadata.get("credit"),
            transaction_type=metadata.get("transaction_type"),
            date=metadata.get("date"),
            days_from_today=metadata.get("days_from_today"),
            is_within_3_months=metadata.get("is_within_3_months"),
            category=metadata.get("category"),
            transaction_number=metadata.get("transaction_number") or metadata.get("receipt_number"),
        ),
    )


def extract_transactions_from_text(
    text: str,
    *,
    source_name: str | None = None,
    only_bills: bool = False,
) -> list[TransactionRecord]:
    normalized = _normalize_text(text)
    if not normalized:
        return []

    source = source_name or "extracted_text"
    candidates: list[tuple[str, dict[str, Any]]] = []

    for line in [ln.strip() for ln in normalized.splitlines() if ln.strip()]:
        parsed = _parse_transaction_row(line)
        if parsed is not None:
            candidates.append(parsed)
            continue
        if _looks_like_transaction_line(line):
            meta = extract_receipt_metadata_from_text(line)
            meta["category"] = categorise_transaction(line, meta.get("receipt_type"))
            candidates.append((line, meta))

    if not candidates:
        meta = extract_receipt_metadata_from_text(normalized)
        meta["category"] = categorise_transaction(normalized, meta.get("receipt_type"))
        candidates = [(normalized, meta)]

    # Inherit neutral doc-level fields (never receipt_type — prevents category bleed)
    doc_meta = extract_receipt_metadata_from_text(normalized)
    records: list[TransactionRecord] = []
    for description, meta in candidates:
        for k in ("receipt_number", "address", "days_from_today", "is_within_3_months"):
            if meta.get(k) is None and doc_meta.get(k) is not None:
                meta[k] = doc_meta[k]
        records.append(_build_record(description, meta, source))

    if only_bills:
        records = [r for r in records if _is_bill_transaction(r)]

    return records


def extract_transactions_from_file(
    file_path: str | Path,
    *,
    source_name: str | None = None,
    only_bills: bool = False,
) -> list[TransactionRecord]:
    path = Path(file_path)
    if not path.exists():
        return []
    if path.suffix.lower() == ".pdf":
        return extract_transactions_from_pdf_bytes(
            path.read_bytes(),
            filename=path.name,
            source_name=source_name or path.stem,
            only_bills=only_bills,
        )
    return extract_transactions_from_text(
        path.read_text(encoding="utf-8", errors="ignore"),
        source_name=source_name or path.stem,
        only_bills=only_bills,
    )


def extract_transactions_from_pdf_bytes(
    pdf_bytes: bytes,
    *,
    filename: str | None = None,
    source_name: str | None = None,
    only_bills: bool = False,
) -> list[TransactionRecord]:
    from services.receipt import extract_receipt_text_from_file_bytes

    text, _src, _meta = extract_receipt_text_from_file_bytes(
        pdf_bytes,
        filename=filename or "document.pdf",
        content_type="application/pdf",
    )
    return extract_transactions_from_text(
        text,
        source_name=source_name or (Path(filename).stem if filename else "pdf_document"),
        only_bills=only_bills,
    )


def extract_transactions_from_data_dir(
    data_dir: str | Path,
    *,
    only_bills: bool = False,
) -> list[TransactionRecord]:
    directory = Path(data_dir)
    if not directory.exists():
        return []
    all_txs: list[TransactionRecord] = []
    for path in sorted(list(directory.glob("*.txt")) + list(directory.glob("*.pdf"))):
        all_txs.extend(extract_transactions_from_file(path, only_bills=only_bills))
    return all_txs


def export_transactions_from_data_dir(
    data_dir: str | Path,
    output_path: str | Path,
    *,
    only_bills: bool = False,
) -> Path:
    return write_transactions_to_csv(
        extract_transactions_from_data_dir(data_dir, only_bills=only_bills),
        output_path,
    )


def write_transactions_to_csv(
    transactions: list[TransactionRecord],
    output_path: str | Path,
) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "source", "description", "transaction_number", "amount",
        "debit", "credit", "transaction_type", "category",
        "date", "days_from_today", "is_within_3_months",
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
                "transaction_type": m.transaction_type or "",
                "category": m.category or "",
                "date": m.date or "",
                "days_from_today": m.days_from_today if m.days_from_today is not None else "",
                "is_within_3_months": m.is_within_3_months if m.is_within_3_months is not None else "",
            })

    return destination


extract_bills_from_text = extract_transactions_from_text
extract_bills_from_file = extract_transactions_from_file
extract_bills_from_data_dir = extract_transactions_from_data_dir
export_bills_from_data_dir = export_transactions_from_data_dir
write_bills_to_csv = write_transactions_to_csv
