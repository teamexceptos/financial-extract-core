from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from utils.receipt_metadata import extract_receipt_metadata_from_text


class PdfInspectorExtractionError(RuntimeError):
    pass


@dataclass
class PdfInspectorReadResult:
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
    except Exception as exc:  # pragma: no cover - import guard
        raise PdfInspectorExtractionError("pdf_inspector is not available") from exc
    return pdf_inspector


def _markdown_to_text(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n")
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"(?m)^\s*\d+\.\s+", "", text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_pdf_bytes_with_pdf_inspector(
    file_bytes: bytes,
    *,
    pages: Sequence[int] | None = None,
    min_text_length: int = 50,
) -> PdfInspectorReadResult:
    if not file_bytes:
        raise PdfInspectorExtractionError("Empty PDF")

    pdf_inspector = _get_pdf_inspector()
    page_list = list(pages) if pages is not None else None

    try:
        result = pdf_inspector.process_pdf_bytes(file_bytes, pages=page_list)
    except Exception as exc:
        raise PdfInspectorExtractionError("Failed to inspect PDF with pdf_inspector") from exc

    markdown = result.markdown or ""
    text = _markdown_to_text(markdown) if markdown else ""
    
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

    source = "pdf_inspector_text"
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
        is_complex_layout=bool(getattr(result, "is_complex_layout", False)),
        metadata=metadata,
    )


def extract_receipt_text_from_pdf_bytes(
    file_bytes: bytes,
    *,
    pages: Sequence[int] | None = None,
    min_text_length: int = 50,
) -> tuple[str, str, dict[str, Any]]:
    result = read_pdf_bytes_with_pdf_inspector(
        file_bytes,
        pages=pages,
        min_text_length=min_text_length,
    )
    return result.text, result.source, result.metadata
