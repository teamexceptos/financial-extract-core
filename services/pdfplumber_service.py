"""
PDF extraction service backed entirely by pdfplumber.

Reconstructs bank statement text from page geometry — no dependency on
pdf-inspector. Preferred for statements where column position carries the
meaning (Kuda, Moniepoint wide exports, VBank).

The public result type and the convenience extractor mirror those of
``pdf_inspector_service`` so callers can swap backends without code changes:

    from services.pdfplumber_service import read_pdf_bytes_with_pdfplumber
    result = read_pdf_bytes_with_pdfplumber(file_bytes)
    # result.text / result.source / result.metadata / result.confidence
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from utils.pdf_layout import (
    LAYOUT_TABLE_DELIMITER,
    LAYOUT_TABLE_SOURCE,
    PAGE_FURNITURE,
    ROW_ANCHOR,
    WIDE_TABLE_SIGNATURE,
    header_columns,
    join_chars,
    row_pitch,
    summary_region,
    text_lines,
)
from utils.receipt_metadata import extract_receipt_metadata_from_text


class PdfPlumberExtractionError(RuntimeError):
    """Raised when pdfplumber-based extraction fails or yields no text."""


@dataclass
class PdfPlumberReadResult:
    """Mirrors PdfInspectorReadResult so callers can use either backend uniformly.

    ``markdown`` is always ``None`` — pdfplumber has no markdown output.
    ``confidence`` is always ``1.0`` — pdfplumber reads the raw glyph stream.
    """
    text: str
    markdown: None = field(default=None, repr=False)
    source: str = LAYOUT_TABLE_SOURCE
    pdf_type: str = "digital"
    confidence: float = 1.0
    pages_needing_ocr: list[int] = field(default_factory=list)
    needs_ocr: bool = False
    is_complex_layout: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def _open_pdf(file_bytes: bytes):
    import pdfplumber
    return pdfplumber.open(io.BytesIO(file_bytes))


def _layout_table_from_pdf(file_bytes: bytes) -> str:
    """Rebuild the transaction table from page geometry.

    Emits three sections joined by newlines:
      1. The statement summary block (account name, balances, totals).
      2. A tab-delimited header row naming each column.
      3. One tab-delimited data row per transaction, blank cells preserved.

    Returns an empty string when no recognisable table header is found.
    """
    try:
        pdf = _open_pdf(file_bytes)
    except Exception:
        return ""

    lines_out: list[str] = []
    rows: list[dict[str, list[dict]]] = []

    try:
        with pdf:
            found_header: tuple[float, float, list[tuple[float, str]]] | None = None
            header_page_idx = -1

            # Scan the first 5 pages for the table header (skips cover pages).
            for idx, page in enumerate(pdf.pages[:5]):
                found_header = header_columns(page)
                if found_header:
                    header_page_idx = idx
                    break
                page.flush_cache()

            if not found_header:
                return ""

            band_top, band_bottom, columns = found_header
            col_names = [name for _, name in columns]

            def column_of(x: float) -> str:
                """Return the column name owning x-coordinate *x*."""
                owner = col_names[0]
                for anchor_x, name in columns:
                    if x >= anchor_x - 3.0:
                        owner = name
                return owner

            for idx, page in enumerate(pdf.pages):
                chars = [c for c in page.chars if c.get("text")]
                # Stamp page index so join_chars can preserve cross-page row order.
                for char in chars:
                    char["_seq"] = idx

                if idx == header_page_idx:
                    # Prepend summary block so it leads the output.
                    lines_out = summary_region(page, band_top, band_bottom) + lines_out
                    # Drop header band itself from character stream.
                    chars = [c for c in chars if c["top"] > band_bottom]

                page_lines = text_lines(chars)
                kept = {
                    top: row for top, row in page_lines.items()
                    if not PAGE_FURNITURE.match(join_chars(row))
                }

                # Identify y-positions that open a new transaction row.
                anchors = sorted(
                    top for top, row in kept.items()
                    if ROW_ANCHOR.match(
                        join_chars([c for c in row if column_of(c["x0"]) == col_names[0]])
                    )
                )
                if not anchors:
                    page.flush_cache()
                    continue

                # Build row-boundary splits at the midpoints between anchors.
                splits = [
                    (anchors[k] + anchors[k + 1]) / 2
                    for k in range(len(anchors) - 1)
                ]
                # Half the median row pitch determines the top boundary of the first row.
                # Rows broken across a page break resume near the top of the next page,
                # which is much further above the first anchor than a wrapped cell.
                pitch = row_pitch(anchors) or 20.0
                splits.insert(0, anchors[0] - pitch / 2)

                # Assign each character to its row band and column.
                bands: dict[int, dict[str, list[dict]]] = {}
                for top, row in kept.items():
                    band_idx = sum(1 for split in splits if top > split)
                    cells = bands.setdefault(band_idx, {})
                    for char in row:
                        cells.setdefault(column_of(char["x0"]), []).append(char)

                for band_idx in sorted(bands):
                    cells = bands[band_idx]
                    first_col_text = join_chars(cells.get(col_names[0], []))[:11] or "x"
                    opens_row = ROW_ANCHOR.match(first_col_text)
                    if band_idx == 0 and rows and not opens_row:
                        # Content above the first anchor on this page is a continuation
                        # of the last row from the previous page.
                        for name, chars_in in cells.items():
                            rows[-1].setdefault(name, []).extend(chars_in)
                    else:
                        rows.append(cells)

                page.flush_cache()

    except Exception:
        return ""

    if not rows:
        return ""

    lines_out.append(LAYOUT_TABLE_DELIMITER.join(col_names))
    for row in rows:
        lines_out.append(
            LAYOUT_TABLE_DELIMITER.join(join_chars(row.get(name, [])) for name in col_names)
        )
    return "\n".join(lines_out)


def _plain_text_from_pdf(file_bytes: bytes) -> str:
    """Extract flat reading-order text from every page via pdfplumber."""
    try:
        pdf = _open_pdf(file_bytes)
        parts: list[str] = []
        with pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t.strip())
                page.flush_cache()
        return "\n\n".join(parts)
    except Exception:
        return ""


def read_pdf_bytes_with_pdfplumber(
    file_bytes: bytes,
    *,
    min_text_length: int = 50,
) -> PdfPlumberReadResult:
    """Extract text from a PDF using pdfplumber's geometry engine.

    Strategy:
    - If plain text is empty or the statement uses wide/optional columns,
      attempt geometry-based layout reconstruction (required for Kuda, wide
      Moniepoint exports).
    - Otherwise try geometry first, fall back to flat text if no table header
      is found.

    Raises ``PdfPlumberExtractionError`` when no text can be extracted at all.
    """
    if not file_bytes:
        raise PdfPlumberExtractionError("Empty PDF")

    text = ""
    source = LAYOUT_TABLE_SOURCE
    is_layout = False

    try:
        plain = _plain_text_from_pdf(file_bytes)
        wide_table = bool(WIDE_TABLE_SIGNATURE.search(plain))

        if not plain.strip() or wide_table:
            # Geometry layout is mandatory.
            layout = _layout_table_from_pdf(file_bytes)
            if layout:
                text = layout
                is_layout = True
            elif plain.strip():
                text = plain
                source = "pdfplumber_text"
        else:
            # Geometry is preferred; plain text is the fallback.
            layout = _layout_table_from_pdf(file_bytes)
            if layout:
                text = layout
                is_layout = True
            else:
                text = plain
                source = "pdfplumber_text"

    except PdfPlumberExtractionError:
        raise
    except Exception as exc:
        raise PdfPlumberExtractionError(f"pdfplumber extraction failed: {exc}") from exc

    text = text.strip()
    if not text:
        raise PdfPlumberExtractionError("No text extracted from PDF via pdfplumber")

    if not is_layout:
        source = "pdfplumber_text"

    metadata = extract_receipt_metadata_from_text(text)
    metadata["confidence"] = 1.0

    return PdfPlumberReadResult(
        text=text,
        source=source,
        pdf_type="digital",
        confidence=1.0,
        pages_needing_ocr=[],
        needs_ocr=False,
        is_complex_layout=is_layout,
        metadata=metadata,
    )


def extract_receipt_text_from_pdf_bytes(
    file_bytes: bytes,
    *,
    min_text_length: int = 50,
) -> tuple[str, str, dict[str, Any]]:
    """Convenience wrapper matching the pdf_inspector_service equivalent signature."""
    result = read_pdf_bytes_with_pdfplumber(file_bytes, min_text_length=min_text_length)
    return result.text, result.source, result.metadata
