"""
PDF extraction service backed by pdf-inspector (with pdfplumber geometry fallback).

pdf-inspector parses PDF structure and emits markdown + confidence metadata.
When its markdown tables are corrupted by cell values containing "|", or when
the statement layout cannot survive a flat text rendering, this service falls
back to the geometry-based layout from ``pdfplumber_service``.

Public types and helpers
------------------------
- ``PdfInspectorReadResult`` — result dataclass (confidence, source, metadata …)
- ``read_pdf_bytes_with_pdf_inspector`` — primary entry point
- ``extract_receipt_text_from_pdf_bytes`` — thin tuple wrapper for legacy callers

Geometry helpers (``_text_lines``, ``_join``, ``header_columns``, …) are now
in ``utils/pdf_layout`` and shared with ``pdfplumber_service``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from utils.pdf_layout import (
    LAYOUT_TABLE_SOURCE,
    MD_TABLE_SEPARATOR,
    PIPE_IN_CELL_TOKEN,
    WIDE_TABLE_SIGNATURE,
)
from utils.receipt_metadata import extract_receipt_metadata_from_text


class PdfInspectorExtractionError(RuntimeError):
    """Raised when pdf-inspector extraction fails or yields no text."""


@dataclass
class PdfInspectorReadResult:
    """Result of a pdf-inspector extraction pass.

    Fields mirror ``PdfPlumberReadResult`` so callers can treat both backends
    uniformly — just swap the read function.
    """
    text: str
    markdown: str | None
    source: str
    pdf_type: str
    confidence: float
    pages_needing_ocr: list[int]
    needs_ocr: bool
    is_complex_layout: bool
    metadata: dict[str, Any]


def _get_pdf_inspector():
    try:
        import pdf_inspector
    except Exception as exc:
        raise PdfInspectorExtractionError("pdf_inspector is not available") from exc
    return pdf_inspector


def _markdown_to_text(markdown: str) -> str:
    """Convert pdf-inspector markdown output to plain text.

    Strips heading markers, list bullets, and inline code ticks so the result
    reads like a bank statement's raw text rather than a formatted document.
    """
    text = markdown.replace("\r\n", "\n")
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"(?m)^\s*\d+\.\s+", "", text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _markdown_tables_are_unreliable(markdown: str) -> bool:
    """Return True when the PDF's own text contains "|" that corrupts the markdown tables.

    pdf-inspector renders detected tables as markdown pipe tables. When cell
    values themselves contain "|" (as Moniepoint transaction references do)
    the rendered rows no longer round-trip: columns shift and amounts detach
    from their transaction. In that case the plain positional text layout,
    which preserves reading order verbatim, is the faithful representation.
    """
    if not markdown:
        return False
    if not MD_TABLE_SEPARATOR.search(markdown):
        return False
    return len(PIPE_IN_CELL_TOKEN.findall(markdown)) >= 3


def read_pdf_bytes_with_pdf_inspector(
    file_bytes: bytes,
    *,
    pages: Sequence[int] | None = None,
    min_text_length: int = 50,
) -> PdfInspectorReadResult:
    """Extract text from a PDF using pdf-inspector with pdfplumber geometry fallback.

    Extraction strategy (evaluated in order):
    1. Run pdf-inspector to get markdown + plain text.
    2. If the plain text is empty or the statement uses wide/optional columns
       (``WIDE_TABLE_SIGNATURE``), attempt geometry-based layout reconstruction
       via ``pdfplumber_service._layout_table_from_pdf``.
    3. If the markdown tables contain "|" in cell values (corrupted), fall back
       to the plain positional text from pdf-inspector.
    4. If no text at all, try pdf-inspector's plain ``extract_text_bytes``.

    Raises ``PdfInspectorExtractionError`` when no text can be recovered.
    """
    if not file_bytes:
        raise PdfInspectorExtractionError("Empty PDF")

    pdf_inspector = _get_pdf_inspector()
    page_list = list(pages) if pages is not None else None

    try:
        result = pdf_inspector.process_pdf_bytes(file_bytes, pages=page_list)
    except Exception as exc:
        raise PdfInspectorExtractionError(
            "Failed to inspect PDF with pdf_inspector"
        ) from exc

    markdown = result.markdown or ""
    text = _markdown_to_text(markdown) if markdown else ""
    layout_table = False

    # Geometry fallback — only when processing the whole document (no page filter).
    if page_list is None:
        try:
            plain = pdf_inspector.extract_text_bytes(file_bytes) or ""
        except Exception:
            plain = ""

        wide_table = bool(WIDE_TABLE_SIGNATURE.search(plain or text))

        if not plain.strip() or wide_table:
            # Either no plain text exists or the column positions carry meaning.
            # Rebuild the table from the page geometry.
            from services.pdfplumber_service import _layout_table_from_pdf  # local import avoids circulars
            rebuilt = _layout_table_from_pdf(file_bytes)
            if rebuilt:
                text = rebuilt
                layout_table = True
            elif plain.strip() and _markdown_tables_are_unreliable(markdown):
                text = plain
        elif text and plain.strip() and _markdown_tables_are_unreliable(markdown):
            # Cell values contain "|" — markdown tables are corrupt. Use plain text.
            text = plain

    # Final fallback: ask pdf-inspector for raw plain text.
    if not text:
        try:
            if page_list is None:
                text = pdf_inspector.extract_text_bytes(file_bytes) or ""
        except Exception:
            pass

    text = text.strip()
    pdf_type = getattr(result, "pdf_type", "unknown")
    pages_needing_ocr = getattr(result, "pages_needing_ocr", [])
    needs_ocr = pdf_type in {"scanned", "image_based"} or bool(pages_needing_ocr)

    if not text:
        raise PdfInspectorExtractionError("No text extracted from PDF")

    source = LAYOUT_TABLE_SOURCE if layout_table else "pdf_inspector_text"
    if needs_ocr and len(text) < min_text_length:
        source = "pdf_inspector_partial_text"

    confidence_val = float(getattr(result, "confidence", 0.0))
    metadata = extract_receipt_metadata_from_text(text)
    metadata["confidence"] = confidence_val

    return PdfInspectorReadResult(
        text=text,
        markdown=markdown if markdown else None,
        source=source,
        pdf_type=pdf_type,
        confidence=confidence_val,
        pages_needing_ocr=pages_needing_ocr,
        needs_ocr=needs_ocr,
        is_complex_layout=layout_table,
        metadata=metadata,
    )


def extract_receipt_text_from_pdf_bytes(
    file_bytes: bytes,
    *,
    pages: Sequence[int] | None = None,
    min_text_length: int = 50,
) -> tuple[str, str, dict[str, Any]]:
    """Convenience tuple wrapper used by legacy callers (receipt.py, tests)."""
    result = read_pdf_bytes_with_pdf_inspector(
        file_bytes,
        pages=pages,
        min_text_length=min_text_length,
    )
    return result.text, result.source, result.metadata
