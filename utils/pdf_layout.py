"""
PDF geometry layout helpers — shared across pdf_inspector_service and pdfplumber_service.

These functions operate on raw pdfplumber page / character data to reconstruct
the spatial layout of bank statement tables, recovering column boundaries, row
pitch, and summary header blocks that are lost in flat text renderings.

Nothing in this module depends on pdf-inspector or the service layer, so it
can be imported safely from any extraction backend.
"""
from __future__ import annotations

import re

LAYOUT_TABLE_SOURCE = "pdfplumber_layout_table"

# Valid backend identifiers.
PDF_BACKEND_PDF_INSPECTOR = "pdf_inspector"
PDF_BACKEND_PDFPLUMBER = "pdfplumber"
_VALID_BACKENDS = {PDF_BACKEND_PDF_INSPECTOR, PDF_BACKEND_PDFPLUMBER}

# Tab is used as the cell delimiter because "|" appears inside Moniepoint
# reference tokens (e.g. ``TRF|2MPT303id|..._DEBIT_0``) and would corrupt
# column splits if used as a separator.
LAYOUT_TABLE_DELIMITER = "\t"

# Known column header labels from Nigerian bank statement table headers.
# A page band scoring ≥ 4 of these is treated as the transaction table header.
TABLE_HEADER_LABELS: frozenset[str] = frozenset({
    "date/time", "date", "money in", "money out", "category", "to / from",
    "description", "balance", "narration", "reference", "debit", "credit",
    "account name", "transaction type", "transaction status", "terminal id", "rrn",
    "transaction ref", "reversal status", "transaction amount", "settlement debit",
    "settlement credit", "balance before", "balance after", "charge", "beneficiary",
    "beneficiary institution", "source", "source institution",
})

# First-column value that opens a new transaction row.
# Kuda: "23/01/24"  |  Moniepoint: "2025-10-07T09:…"
ROW_ANCHOR = re.compile(r"^(?:\d{2}/\d{2}/\d{2,4}|\d{4}-|\d{4}-\d{2}-\d{2}\S*)$")

# Page furniture appearing inside the table x-range but not row data.
PAGE_FURNITURE = re.compile(
    r"^(?:Kuda MF Bank|(?:is )?licensed by|Lagos\.\s*Nigeria|Account Number\s*:"
    r"|Page \d+ of \d+|Statement$)"
)

# Statements where the transaction table only survives in page geometry
# (wide exports with optional blank columns).
WIDE_TABLE_SIGNATURE = re.compile(
    r"settlement\s+debit.*?settlement\s+credit|balance\s+before.*?balance\s+after",
    re.IGNORECASE | re.DOTALL,
)

# Reference tokens that embed a literal "|" inside a cell value, e.g.
# ``TRF|2MPT8f877|1975482030321524736_DEBIT_0`` (Moniepoint / TeamApt).
# When these appear in a markdown pipe table the column boundaries shift.
PIPE_IN_CELL_TOKEN = re.compile(
    r"(?:[A-Z_]{2,}\|[A-Za-z0-9|#/\-]{4,}_(?:BUSINESS_|CBA_)?(?:CREDIT|DEBIT)_\d)"
)
MD_TABLE_SEPARATOR = re.compile(r"(?m)^\s*\|(?:\s*-{3,}\s*\|)+\s*$")


def text_lines(chars: list[dict]) -> dict[float, list[dict]]:
    """Group pdfplumber characters into physical text lines keyed by baseline y."""
    lines: dict[float, list[dict]] = {}
    for char in chars:
        lines.setdefault(round(char["top"], 1), []).append(char)
    return lines


def join_chars(chars: list[dict]) -> str:
    """Join pdfplumber characters in reading order, restoring implicit spaces.

    Each character should have a ``_seq`` attribute (page index) stamped on it
    before calling so rows split across page breaks are reassembled in document
    order rather than jumping to the top of the next page.
    """
    ordered = sorted(
        chars,
        key=lambda c: (c.get("_seq", 0), round(c["top"], 1), c["x0"]),
    )
    out: list[str] = []
    previous: dict | None = None
    for char in ordered:
        if previous is not None:
            same_line = (
                char.get("_seq", 0) == previous.get("_seq", 0)
                and abs(round(char["top"], 1) - round(previous["top"], 1)) < 0.05
            )
            gap = char["x0"] - previous["x1"]
            if not same_line or gap > 1.2:
                out.append(" ")
        out.append(char["text"])
        previous = char
    return re.sub(r"\s+", " ", "".join(out)).strip()


def row_pitch(anchor_tops: list[float]) -> float | None:
    """Return the median vertical gap between consecutive transaction rows."""
    gaps = sorted(
        anchor_tops[k + 1] - anchor_tops[k] for k in range(len(anchor_tops) - 1)
    )
    if not gaps:
        return None
    return gaps[len(gaps) // 2]


def header_columns(page) -> tuple[float, float, list[tuple[float, str]]] | None:
    """Locate the transaction table header on a pdfplumber page.

    Returns ``(band_top, band_bottom, columns)`` where *columns* is a sorted
    list of ``(x_anchor, label)`` pairs, or ``None`` if no header is found.

    Column labels may be stacked over several baselines (e.g. "Settlement"
    above "Debit"), so words are first merged per line then grouped by their
    shared left x-coordinate across the band.
    """
    words = page.extract_words(keep_blank_chars=False)
    if not words:
        return None

    lines: dict[float, list[dict]] = {}
    for word in words:
        lines.setdefault(round(word["top"], 1), []).append(word)

    # Merge adjacent words with a tiny gap — "Transaction" + "Ref" → one label.
    merged: dict[float, list[dict]] = {}
    for top, row in lines.items():
        row.sort(key=lambda w: w["x0"])
        cells: list[dict] = []
        for word in row:
            if cells and word["x0"] - cells[-1]["x1"] < 6.0:
                cells[-1] = {
                    "x0": cells[-1]["x0"],
                    "x1": word["x1"],
                    "text": f"{cells[-1]['text']} {word['text']}",
                }
            else:
                cells.append({"x0": word["x0"], "x1": word["x1"], "text": word["text"]})
        merged[top] = cells

    def score(tops: list[float]) -> int:
        return sum(
            1 for t in tops for c in merged[t]
            if c["text"].strip().lower() in TABLE_HEADER_LABELS
        )

    best: tuple[int, list[float]] | None = None
    for top in sorted(merged):
        band = [t for t in merged if top <= t <= top + 16.0]
        hits = score(band)
        if hits >= 4 and (best is None or hits > best[0]):
            best = (hits, band)
    if best is None:
        return None

    band = best[1]
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


def summary_region(page, band_top: float, band_bottom: float) -> list[str]:  # noqa: ARG001
    """Extract the statement summary block (everything above the transaction table).

    Emits raw lines from the cropped page region, then emits explicit
    ``Label: value`` pairs for labels whose value is printed beneath them
    (common in Kuda and similar layouts).
    """
    out: list[str] = []
    try:
        above = page.crop((0, 0, page.width, max(band_top - 2.0, 1.0)))
        text = above.extract_text() or ""
    except Exception:
        text = ""
    out.extend(line.strip() for line in text.split("\n") if line.strip())

    try:
        words = [
            w for w in page.extract_words(keep_blank_chars=False)
            if w["top"] < band_top
        ]
    except Exception:
        return out

    lines: dict[float, list[dict]] = {}
    for word in words:
        lines.setdefault(round(word["top"], 1), []).append(word)
    for row in lines.values():
        row.sort(key=lambda w: w["x0"])

    label_names = (
        "Account", "Date",
        "Opening Balance", "Closing Balance",
        "Money in", "Money out",
    )
    for label in label_names:
        target = label.lower().split()
        found: dict | None = None
        for top, row in lines.items():
            for start in range(len(row)):
                span = row[start : start + len(target)]
                if [w["text"].lower() for w in span] == target:
                    found = {"top": top, "x0": span[0]["x0"]}
                    break
            if found:
                break
        if not found:
            continue
        below = [
            w for row in lines.values() for w in row
            if w["top"] > found["top"] + 2.0
            and abs(w["x0"] - found["x0"]) < 6.0
            and w["text"].lower() not in {lb.lower() for lb in label_names}
        ]
        if below:
            value = min(below, key=lambda w: w["top"])["text"]
            # Only accept values that look numeric — avoids pairing two labels.
            if any(ch.isdigit() for ch in value):
                out.append(f"{label}: {value}")

    # Account holder name from the top-left address block.
    if words:
        left = min(w["x0"] for w in words)
        starts = sorted({round(w["top"], 1) for w in words if abs(w["x0"] - left) < 6.0})
        for top in starts:
            row = sorted((w for w in lines.get(top, [])), key=lambda z: z["x0"])
            name_words: list[str] = []
            previous_x1 = 0.0
            for word in row:
                if name_words and word["x0"] - previous_x1 > 24.0:
                    break
                name_words.append(word["text"])
                previous_x1 = word["x1"]
            name = " ".join(name_words).strip()
            low = name.lower()
            if (
                not name
                or low in TABLE_HEADER_LABELS
                or "statement" in low
                or "summary" in low
            ):
                continue
            out.append(f"Account Name: {name}")
            break
    return out
