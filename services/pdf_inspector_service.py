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


# Reference tokens that embed a literal "|" inside the cell value, e.g.
# ``TRF|2MPT8f877|1975482030321524736_DEBIT_0`` (Moniepoint / TeamApt statements).
# When a document's own text uses "|", the markdown table reconstruction splits a
# single logical row across several bogus cells/rows, silently corrupting the data.
_PIPE_IN_CELL_TOKEN = re.compile(
    r"(?:[A-Z_]{2,}\|[A-Za-z0-9|#/\-]{4,}_(?:BUSINESS_|CBA_)?(?:CREDIT|DEBIT)_\d)"
)
_MD_TABLE_SEPARATOR = re.compile(r"(?m)^\s*\|(?:\s*-{3,}\s*\|)+\s*$")


LAYOUT_TABLE_SOURCE = "pdf_inspector_layout_table"

# Cell delimiter for the rebuilt table. A tab cannot collide with cell content because
# ``_join`` collapses every whitespace run to a single space, whereas "|" appears inside
# Moniepoint references (``TRF|2MPT303id|...``) and narrations (``/ATP|2MPT303id|...``),
# which would make the columns impossible to recover by splitting.
LAYOUT_TABLE_DELIMITER = "\t"

# Column labels seen in the statement layouts whose columns the text renderings lose.
# A header row is the run of these labels sharing a baseline (or a few stacked baselines,
# where a label such as "Settlement Debit" is drawn over two lines).
_TABLE_HEADER_LABELS = frozenset({
    "date/time", "date", "money in", "money out", "category", "to / from",
    "description", "balance", "narration", "reference", "debit", "credit",
    "account name", "transaction type", "transaction status", "terminal id", "rrn",
    "transaction ref", "reversal status", "transaction amount", "settlement debit",
    "settlement credit", "balance before", "balance after", "charge", "beneficiary",
    "beneficiary institution", "source", "source institution",
})
# The first column's value on the physical line that opens a new transaction. Kuda prints
# "23/01/24"; Moniepoint's wide export wraps the timestamp and opens with "2025-".
_ROW_ANCHOR = re.compile(r"^(?:\d{2}/\d{2}/\d{2,4}|\d{4}-|\d{4}-\d{2}-\d{2}\S*)$")
# Repeated page furniture that sits inside the table's x-range but is not table content.
_PAGE_FURNITURE = re.compile(
    r"^(?:Kuda MF Bank|(?:is )?licensed by|Lagos\.\s*Nigeria|Account Number\s*:"
    r"|Page \d+ of \d+|Statement$)"
)
# Statement families whose transaction table survives only in the page geometry.
_WIDE_TABLE_SIGNATURE = re.compile(
    r"settlement\s+debit.*?settlement\s+credit|balance\s+before.*?balance\s+after",
    re.IGNORECASE | re.DOTALL,
)


def _open_pdf(file_bytes: bytes):
    import io

    import pdfplumber

    return pdfplumber.open(io.BytesIO(file_bytes))


def _text_lines(chars: list[dict]) -> dict[float, list[dict]]:
    """Group characters into physical text lines keyed by their baseline."""
    lines: dict[float, list[dict]] = {}
    for char in chars:
        lines.setdefault(round(char["top"], 1), []).append(char)
    return lines


def _join(chars: list[dict]) -> str:
    """Join characters in reading order, restoring the spaces the glyph runs omit.

    ``_seq`` carries the page a character came from, so a row continued after a page
    break keeps reading in document order rather than jumping to the top of the page.
    """
    ordered = sorted(chars, key=lambda c: (c.get("_seq", 0), round(c["top"], 1), c["x0"]))
    out: list[str] = []
    previous: dict | None = None
    for char in ordered:
        if previous is not None:
            same_line = (char.get("_seq", 0) == previous.get("_seq", 0)
                         and abs(round(char["top"], 1) - round(previous["top"], 1)) < 0.05)
            gap = char["x0"] - previous["x1"]
            if not same_line or gap > 1.2:
                out.append(" ")
        out.append(char["text"])
        previous = char
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _header_columns(page) -> tuple[float, float, list[tuple[float, str]]] | None:
    """Find the transaction table header and return (band_top, band_bottom, columns).

    Labels may be stacked over several baselines ("Settlement" above "Debit"), so words
    are first merged along each line, then grouped down the page by their left edge.
    """
    words = page.extract_words(keep_blank_chars=False)
    if not words:
        return None

    lines: dict[float, list[dict]] = {}
    for word in words:
        lines.setdefault(round(word["top"], 1), []).append(word)

    # Merge words separated by a hair's breadth: "Transaction" + "Ref" is one label.
    merged: dict[float, list[dict]] = {}
    for top, row in lines.items():
        row.sort(key=lambda w: w["x0"])
        cells: list[dict] = []
        for word in row:
            if cells and word["x0"] - cells[-1]["x1"] < 6.0:
                cells[-1] = {"x0": cells[-1]["x0"], "x1": word["x1"],
                             "text": f"{cells[-1]['text']} {word['text']}"}
            else:
                cells.append({"x0": word["x0"], "x1": word["x1"], "text": word["text"]})
        merged[top] = cells

    def score(tops: list[float]) -> int:
        return sum(1 for t in tops for c in merged[t]
                   if c["text"].strip().lower() in _TABLE_HEADER_LABELS)

    best: tuple[int, list[float]] | None = None
    for top in sorted(merged):
        band = [t for t in merged if top <= t <= top + 16.0]
        hits = score(band)
        if hits >= 4 and (best is None or hits > best[0]):
            best = (hits, band)
    if best is None:
        return None

    band = best[1]
    # A label stacked over several lines shares one left edge.
    grouped: dict[float, list[tuple[float, str]]] = {}
    for top in sorted(band):
        for cell in merged[top]:
            key = next((k for k in grouped if abs(k - cell["x0"]) < 3.0), cell["x0"])
            grouped.setdefault(key, []).append((top, cell["text"]))
    columns = sorted(
        (x, " ".join(text for _, text in sorted(parts)).strip())
        for x, parts in grouped.items()
    )
    if len(columns) < 4:
        return None
    return min(band), max(band) + 4.0, columns


def _layout_table_from_pdf(file_bytes: bytes) -> str:
    """Rebuild a statement's transaction table from the page geometry.

    Some statements only carry their meaning in the layout: Kuda prints debit and credit
    in two separate columns, so an amount's column *is* its direction, and Moniepoint's
    wide export leaves optional columns blank so a value's position is the only thing that
    identifies it. Neither survives the text renderings — Kuda yields no plain text at all,
    and both lose the column boundaries.

    Emits the summary region, then a header line, then one tab-delimited line per
    transaction with empty cells preserved, so extractors address cells by name.
    Returns "" when no transaction-table header is found.
    """
    try:
        pdf = _open_pdf(file_bytes)
    except Exception:
        return ""

    lines_out: list[str] = []
    rows: list[dict[str, list[dict]]] = []
    try:
        with pdf:
            header: tuple[float, float, list[tuple[float, str]]] | None = None
            header_page = -1
            for index, page in enumerate(pdf.pages[:5]):
                header = _header_columns(page)
                if header:
                    header_page = index
                    break
                page.flush_cache()
            if not header:
                return ""

            band_top, band_bottom, columns = header
            names = [name for _, name in columns]

            def column_of(x: float) -> str:
                """Cells are left-aligned to their anchor, so a column owns [anchor, next)."""
                found = names[0]
                for anchor_x, name in columns:
                    if x >= anchor_x - 3.0:
                        found = name
                return found

            for index, page in enumerate(pdf.pages):
                chars = [c for c in page.chars if c.get("text")]
                for char in chars:
                    char["_seq"] = index
                if index == header_page:
                    lines_out = _summary_region(page, band_top, band_bottom) + lines_out
                    chars = [c for c in chars if c["top"] > band_bottom]

                lines = _text_lines(chars)
                keep = {
                    top: row for top, row in lines.items()
                    if not _PAGE_FURNITURE.match(_join(row))
                }

                anchors = sorted(
                    top for top, row in keep.items()
                    if _ROW_ANCHOR.match(
                        _join([c for c in row if column_of(c["x0"]) == names[0]])
                    )
                )
                if not anchors:
                    page.flush_cache()
                    continue

                splits = [(anchors[k] + anchors[k + 1]) / 2 for k in range(len(anchors) - 1)]
                # A row broken by a page break resumes near the top of the next page, far
                # above that page's first anchor — much further than a row's own wrapped
                # cell sits above its date. Half the row pitch separates the two cases.
                pitch = _row_pitch(anchors) or 20.0
                splits.insert(0, anchors[0] - pitch / 2)

                bands: dict[int, dict[str, list[dict]]] = {}
                for top, row in keep.items():
                    band = sum(1 for split in splits if top > split)
                    cells = bands.setdefault(band, {})
                    for char in row:
                        cells.setdefault(column_of(char["x0"]), []).append(char)

                for band in sorted(bands):
                    cells = bands[band]
                    opens_row = _ROW_ANCHOR.match(_join(cells.get(names[0], []))[:11] or "x")
                    if band == 0 and rows and not opens_row:
                        for name, chars_in in cells.items():
                            rows[-1].setdefault(name, []).extend(chars_in)
                    else:
                        rows.append(cells)
                page.flush_cache()
    except Exception:
        return ""

    if not rows:
        return ""

    lines_out.append(LAYOUT_TABLE_DELIMITER.join(names))
    for row in rows:
        lines_out.append(LAYOUT_TABLE_DELIMITER.join(
            _join(row.get(name, [])) for name in names))
    return "\n".join(lines_out)


def _summary_region(page, band_top: float, band_bottom: float) -> list[str]:
    """Render everything above the transaction table: the statement's summary block.

    Emitted twice over: once as the page reads, which suits statements that print a label
    beside its value, and once as ``Label: value`` pairs, which suits statements that print
    the value directly beneath its label.
    """
    out: list[str] = []
    try:
        above = page.crop((0, 0, page.width, max(band_top - 2.0, 1.0)))
        text = above.extract_text() or ""
    except Exception:
        text = ""
    out.extend(line.strip() for line in text.split("\n") if line.strip())

    try:
        words = [w for w in page.extract_words(keep_blank_chars=False)
                 if w["top"] < band_top]
    except Exception:
        return out

    lines: dict[float, list[dict]] = {}
    for word in words:
        lines.setdefault(round(word["top"], 1), []).append(word)
    for row in lines.values():
        row.sort(key=lambda w: w["x0"])

    labels = ("Account", "Date", "Opening Balance", "Closing Balance",
              "Money in", "Money out")
    for label in labels:
        target = label.lower().split()
        found: dict | None = None
        for top, row in lines.items():
            for start in range(len(row)):
                span = row[start:start + len(target)]
                if [w["text"].lower() for w in span] == target:
                    found = {"top": top, "x0": span[0]["x0"]}
                    break
            if found:
                break
        if not found:
            continue
        below = [w for row in lines.values() for w in row
                 if w["top"] > found["top"] + 2.0 and abs(w["x0"] - found["x0"]) < 6.0
                 and w["text"].lower() not in {l.lower() for l in labels}]
        if below:
            value = min(below, key=lambda w: w["top"])["text"]
            # Only a value that looks like one: statements that print the value beside
            # its label instead of beneath it would otherwise pair up two labels.
            if any(ch.isdigit() for ch in value):
                out.append(f"{label}: {value}")

    # The account holder's name heads the left-hand address block.
    if words:
        left = min(w["x0"] for w in words)
        starts = sorted({round(w["top"], 1) for w in words if abs(w["x0"] - left) < 6.0})
        for top in starts:
            row = sorted((w for w in lines.get(top, [])), key=lambda z: z["x0"])
            # Stop at the next column: the name occupies the left block alone.
            name_words: list[str] = []
            for word in row:
                if name_words and word["x0"] - previous_x1 > 24.0:
                    break
                name_words.append(word["text"])
                previous_x1 = word["x1"]
            name = " ".join(name_words).strip()
            low = name.lower()
            if not name or low in _TABLE_HEADER_LABELS or "statement" in low or "summary" in low:
                continue
            out.append(f"Account Name: {name}")
            break
    return out


def _row_pitch(anchor_tops: list[float]) -> float | None:
    """Median vertical distance between consecutive transaction rows on a page."""
    gaps = sorted(anchor_tops[k + 1] - anchor_tops[k] for k in range(len(anchor_tops) - 1))
    if not gaps:
        return None
    return gaps[len(gaps) // 2]


def _markdown_tables_are_unreliable(markdown: str) -> bool:
    """True when the PDF's own text contains "|", making markdown tables untrustworthy.

    pdf-inspector renders detected tables as markdown pipe tables. If the underlying
    cell values themselves contain "|" (as Moniepoint transaction references do), the
    rendered rows no longer round-trip: columns shift, rows split and amounts detach
    from their transaction. In that case the plain positional text extraction, which
    preserves reading order verbatim, is the faithful representation.
    """
    if not markdown:
        return False
    if not _MD_TABLE_SEPARATOR.search(markdown):
        return False
    return len(_PIPE_IN_CELL_TOKEN.findall(markdown)) >= 3


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
    layout_table = False

    if page_list is None:
        try:
            plain = pdf_inspector.extract_text_bytes(file_bytes) or ""
        except Exception:
            plain = ""

        # A wide transaction table leaves its optional columns blank, so a value's column
        # is the only thing identifying it — that has to come from the geometry.
        wide_table = bool(_WIDE_TABLE_SIGNATURE.search(plain or text))

        if not plain.strip() or wide_table:
            # Either no plain text layout exists at all, or the columns it flattens carry
            # the meaning. Rebuild the table from the page geometry.
            rebuilt = _layout_table_from_pdf(file_bytes)
            if rebuilt:
                text = rebuilt
                layout_table = True
            elif plain.strip() and _markdown_tables_are_unreliable(markdown):
                text = plain
        elif text and plain.strip() and _markdown_tables_are_unreliable(markdown):
            # The document's cell values contain "|", so the markdown tables are corrupt.
            # Fall back to the positional text layout, which preserves the true row order.
            text = plain

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
