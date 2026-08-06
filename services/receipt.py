from __future__ import annotations

import io
import re
from difflib import SequenceMatcher
from typing import Any

from models.verification import AddressInput, ReceiptCrossRefResult
from services.address import normalize_address
from utils.receipt_metadata import extract_receipt_metadata_from_text

class ReceiptExtractionError(RuntimeError):
    pass


def extract_receipt_text_from_file_bytes(
    file_bytes: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    if not file_bytes:
        raise ReceiptExtractionError("Empty file")

    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    is_pdf = name.endswith(".pdf") or ctype == "application/pdf" or file_bytes[:4] == b"%PDF"

    if is_pdf:
        from services.pdf_inspector_service import read_pdf_bytes_with_pdf_inspector, PdfInspectorExtractionError
        
        try:
            result = read_pdf_bytes_with_pdf_inspector(file_bytes)
        except PdfInspectorExtractionError as e:
            raise ReceiptExtractionError(str(e)) from e
        except Exception as e:
            raise ReceiptExtractionError("Failed to extract from PDF using pdf-inspector") from e
            
        if not result.needs_ocr:
            return result.text, result.source, result.metadata

        # Fallback to OCR for pages that need it
        try:
            from pdf2image import convert_from_bytes
            import pytesseract
        except Exception as e:
            # If OCR is not available but we have some text, return it
            if result.text and len(result.text) >= 50:
                return result.text, result.source, result.metadata
            raise ReceiptExtractionError("PDF requires OCR but pdf2image or pytesseract is not available") from e
            
        try:
            images = convert_from_bytes(file_bytes)
        except Exception as e:
            raise ReceiptExtractionError("Failed to rasterize PDF for OCR (poppler may be missing)") from e
            
        ocr_parts: list[str] = []
        for i, image in enumerate(images):
            # Only OCR the pages that actually need it (0-indexed)
            if not result.pages_needing_ocr or i in result.pages_needing_ocr or result.pdf_type in {"scanned", "image_based"}:
                try:
                    ocr_parts.append(pytesseract.image_to_string(image) or "")
                except Exception:
                    continue
            else:
                # If page didn't need OCR, we can't easily get page text from pdf-inspector directly right now,
                # but pdf-inspector already extracted all text it could.
                # To keep it simple, we just OCR it anyway if it's mixed, or we could just append it.
                pass
                
        ocr_text = "\n".join([t for t in ocr_parts if t.strip()]).strip()
        
        # Combine text from inspector and OCR
        combined_text = (result.text + "\n\n" + ocr_text).strip()
        if not combined_text:
            raise ReceiptExtractionError("No text extracted from PDF")


        receipt_metadata = extract_receipt_metadata_from_text(combined_text)

        return combined_text, "pdf_inspector_with_ocr", receipt_metadata

    try:
        from PIL import Image
    except Exception as e:
        raise ReceiptExtractionError("Pillow (PIL) is not available for image receipts") from e

    try:
        import pytesseract
    except Exception as e:
        raise ReceiptExtractionError("pytesseract is not available for image receipts") from e

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


def cross_reference_receipt_address_service(address: AddressInput, receipt_text: str) -> ReceiptCrossRefResult:
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
