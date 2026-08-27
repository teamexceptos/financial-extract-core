"""
Receipt and bank statement file ingestion service.

Accepts raw file bytes (PDF, image, or text) and returns a tuple of:
    (text: str, source: str, metadata: dict)

PDF extraction supports two backends selectable via the ``backend`` kwarg:

    "pdf_inspector"  (default) — uses pdf-inspector with pdfplumber geometry
                                  fallback for wide-column / pipe-in-cell PDFs.
    "pdfplumber"               — pure pdfplumber geometry reconstruction; no
                                  dependency on pdf-inspector. Preferred when
                                  the caller wants full column fidelity on the
                                  first pass without the fallback overhead.

Images fall back to Tesseract OCR regardless of backend.
"""
from __future__ import annotations

import io
import re
from difflib import SequenceMatcher
from typing import Any

from models.verification import AddressInput, ReceiptCrossRefResult
from services.address import normalize_address
from utils.pdf_layout import PDF_BACKEND_PDF_INSPECTOR, PDF_BACKEND_PDFPLUMBER, _VALID_BACKENDS
from utils.receipt_metadata import extract_receipt_metadata_from_text


class ReceiptExtractionError(RuntimeError):
    pass


def extract_receipt_text_from_file_bytes(
    file_bytes: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
    actual_file: Any | None = None,
    backend: str = PDF_BACKEND_PDF_INSPECTOR,
) -> tuple[str, str, dict[str, Any]]:
    """Extract text from an uploaded file and return (text, source, metadata).

    Args:
        file_bytes:    Raw bytes of the uploaded file.
        filename:      Original filename — used to detect PDF vs. image.
        content_type:  MIME type from the HTTP upload.
        actual_file:   Original UploadFile object (unused, kept for API compat).
        backend:       PDF extraction engine — ``"pdf_inspector"`` (default)
                       or ``"pdfplumber"``.

    Raises:
        ReceiptExtractionError: When extraction fails or the file is empty.
    """
    if not file_bytes:
        raise ReceiptExtractionError("Empty file")

    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    is_pdf = name.endswith(".pdf") or ctype == "application/pdf" or file_bytes[:4] == b"%PDF"

    if is_pdf:
        return _extract_pdf(file_bytes, backend=backend)

    return _extract_image(file_bytes)


def _extract_pdf(
    file_bytes: bytes,
    *,
    backend: str = PDF_BACKEND_PDF_INSPECTOR,
) -> tuple[str, str, dict[str, Any]]:
    """Route PDF bytes to the selected extraction backend."""
    if backend not in _VALID_BACKENDS:
        raise ReceiptExtractionError(
            f"Unknown extraction backend '{backend}'. "
            f"Valid options: {sorted(_VALID_BACKENDS)}"
        )

    if backend == PDF_BACKEND_PDFPLUMBER:
        return _extract_pdf_pdfplumber(file_bytes)

    return _extract_pdf_inspector(file_bytes)


def _extract_pdf_inspector(
    file_bytes: bytes,
) -> tuple[str, str, dict[str, Any]]:
    """Extract PDF text via pdf-inspector (with pdfplumber geometry fallback)."""
    from services.pdf_inspector_service import (
        PdfInspectorExtractionError,
        read_pdf_bytes_with_pdf_inspector,
    )

    try:
        result = read_pdf_bytes_with_pdf_inspector(file_bytes)
    except PdfInspectorExtractionError as e:
        raise ReceiptExtractionError(str(e)) from e
    except Exception as e:
        raise ReceiptExtractionError(
            "Failed to extract from PDF using pdf-inspector"
        ) from e

    if not result.needs_ocr:
        return result.text, result.source, result.metadata

    # Scanned/image-based PDF — supplement with Tesseract OCR.
    return _ocr_supplement(file_bytes, result)


def _extract_pdf_pdfplumber(
    file_bytes: bytes,
) -> tuple[str, str, dict[str, Any]]:
    """Extract PDF text via pdfplumber geometry engine."""
    from services.pdfplumber_service import (
        PdfPlumberExtractionError,
        read_pdf_bytes_with_pdfplumber,
    )

    try:
        result = read_pdf_bytes_with_pdfplumber(file_bytes)
    except PdfPlumberExtractionError as e:
        raise ReceiptExtractionError(str(e)) from e
    except Exception as e:
        raise ReceiptExtractionError(
            "Failed to extract from PDF using pdfplumber"
        ) from e

    # pdfplumber works directly from glyph streams — OCR is not attempted.
    return result.text, result.source, result.metadata


def _ocr_supplement(file_bytes: bytes, result: Any) -> tuple[str, str, dict[str, Any]]:
    """Run Tesseract OCR on scanned pages and merge with pdf-inspector text."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except Exception as e:
        # OCR unavailable — return whatever text we already have if it's usable.
        if result.text and len(result.text) >= 50:
            return result.text, result.source, result.metadata
        raise ReceiptExtractionError(
            "PDF requires OCR but pdf2image or pytesseract is not available"
        ) from e

    try:
        images = convert_from_bytes(file_bytes)
    except Exception as e:
        raise ReceiptExtractionError(
            "Failed to rasterize PDF for OCR (poppler may be missing)"
        ) from e

    ocr_parts: list[str] = []
    for i, image in enumerate(images):
        needs_this_page = (
            not result.pages_needing_ocr
            or i in result.pages_needing_ocr
            or result.pdf_type in {"scanned", "image_based"}
        )
        if needs_this_page:
            try:
                ocr_parts.append(pytesseract.image_to_string(image) or "")
            except Exception:
                continue

    ocr_text = "\n".join(t for t in ocr_parts if t.strip()).strip()
    combined = (result.text + "\n\n" + ocr_text).strip()

    if not combined:
        raise ReceiptExtractionError("No text extracted from PDF")

    metadata = extract_receipt_metadata_from_text(combined)
    return combined, "pdf_inspector_with_ocr", metadata


def _extract_image(file_bytes: bytes) -> tuple[str, str, dict[str, Any]]:
    """Extract text from an image file via Tesseract OCR."""
    try:
        from PIL import Image
    except Exception as e:
        raise ReceiptExtractionError("Pillow (PIL) is not available for image receipts") from e

    try:
        import pytesseract
    except Exception as e:
        raise ReceiptExtractionError(
            "pytesseract is not available for image receipts"
        ) from e

    try:
        image = Image.open(io.BytesIO(file_bytes))
    except Exception as e:
        raise ReceiptExtractionError("Unsupported image format") from e

    try:
        text = (pytesseract.image_to_string(image) or "").strip()
    except Exception as e:
        raise ReceiptExtractionError("OCR failed for image receipt") from e

    if not text:
        raise ReceiptExtractionError("No text extracted from image")

    return text, "image_ocr", extract_receipt_metadata_from_text(text)


def cross_reference_receipt_address_service(
    address: AddressInput,
    receipt_text: str,
) -> ReceiptCrossRefResult:
    normalized = normalize_address(address)
    receipt_lower = receipt_text.lower()
    address_lower = normalized.lower()

    similarity = SequenceMatcher(None, address_lower, receipt_lower).ratio()

    signals: list[str] = []
    postal = address.postal_code.strip()
    if postal and postal.lower() in receipt_lower:
        signals.append("postal_code_found")
        similarity = max(similarity, 0.75)

    city = address.city.strip()
    if city and city.lower() in receipt_lower:
        signals.append("city_found")
        similarity = max(similarity, 0.65)

    line1_tokens = [t for t in re.split(r"[\s,]+", address.line1.strip().lower()) if len(t) >= 3]
    token_hits = sum(1 for t in line1_tokens if t in receipt_lower)
    if line1_tokens:
        token_ratio = token_hits / len(line1_tokens)
        if token_ratio >= 0.6:
            signals.append("street_tokens_found")
            similarity = max(similarity, 0.7)

    match = similarity >= 0.7

    return ReceiptCrossRefResult(
        normalized_address=normalized,
        similarity_score=round(similarity, 4),
        match=match,
        signals=signals,
    )
