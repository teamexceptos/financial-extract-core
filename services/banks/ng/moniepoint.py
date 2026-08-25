"""Moniepoint Microfinance Bank statement extractor.

Moniepoint statements are laid out as a six column table::

    Date | Narration | Reference | Debit | Credit | Balance

Two properties of the format drive the parsing strategy below:

1. Every cell value can wrap onto several physical lines, and the ``Date`` cell
   *always* wraps (``2025-10-07T09:`` / ``38:48``). A transaction is therefore a
   *block* of lines, not a line.
2. Reference values embed a literal ``|`` (``TRF|2MPT8f877|1975482030321524736_DEBIT_0``).
   This makes any markdown-table rendering of the page unreliable, so the extractor
   works off the positional text layout where each cell occupies its own line.

The block parser is the primary path; a flat single-line parser is kept as a
fallback for pasted text and OCR output where a whole row lands on one line.
"""

from __future__ import annotations

import re
from datetime import date as date_cls
from typing import Any, Iterable

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.date_recency import compute_date_recency
from utils.receipt_metadata import categorise_transaction, classify_transaction_type

# ``2025-10-07T09:`` — the first physical line of a wrapped Date cell.
_DATE_ANCHOR = re.compile(r"^\*{0,2}(\d{4}-\d{2}-\d{2})T(\d{1,2}):?\*{0,2}$")
# ``38:48`` — the second physical line. Moniepoint occasionally truncates it to ``08``.
_TIME_PART = re.compile(r"^\*{0,2}(\d{1,2}:\d{2}(?::\d{2})?|\d{2})\*{0,2}$")
# A money cell: ``0.00``, ``49,900.00``, ``-8.00``.
_AMOUNT_LINE = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d{2}$|^-?\d+\.\d{2}$")
_AMOUNT = r"-?\d{1,3}(?:,\d{3})*\.\d{2}"
# A whole row collapsed onto one line, e.g. pasted text or OCR.
_FLAT_ROW = re.compile(
    r"^\*{0,2}(\d{4}-\d{2}-\d{2})T(\d{1,2}:\d{2}(?::\d{2})?)\*{0,2}\s+"
    r"(.+?)\s+(" + _AMOUNT + r")\s+(" + _AMOUNT + r")\s+(" + _AMOUNT + r")$"
)
# The Reference column value, which is a single whitespace-free token.
_REFERENCE_TOKEN = re.compile(r"_(?:BUSINESS_|CBA_)?(?:CREDIT|DEBIT|DC)_\d+(?:_[A-Z]+)?$")
# Ledger markers unique to the Moniepoint core banking system.
_LEDGER_FINGERPRINT = re.compile(
    r"\b2MPT[A-Za-z0-9]{4,}\b"
    r"|AP_TRSF\|"
    r"|MIT\|(?:HBP|HYD|CRP|TMP)\|"
    r"|SAV\|\d{6,}"
    r"|PUR\|\d{10,}"
    r"|_BUSINESS_(?:CREDIT|DEBIT)_\d"
)

# Ingestion rebuilds a geometry-derived table with tab-delimited cells; "|" cannot be
# used because Moniepoint's own references and narrations contain it.
_CELL = "\t"

_HEADER_LINES = {
    "date", "narration", "reference", "debit", "credit", "balance",
    "account statement", "account summary", "business name", "account number",
    "currency", "address", "opening", "balance", "opening balance",
    "closing balance", "total debits", "total credits", "total debit",
    "total credit", "ngn",
}


class MoniepointExtractor(BaseBankExtractor):
    """Extractor for Moniepoint MFB / TeamApt statements."""

    bank_name: str = "Moniepoint"
    bank_code: str = "moniepoint"
    aliases: tuple[str, ...] = ("monie_point", "teamapt")

    # ------------------------------------------------------------------ detect

    def detect(self, text: str) -> bool:
        head = text[:8000]
        head_lower = head.lower()

        # Statement branding — the issuer, not a counterparty name.
        if re.search(r"moniepoint\s+(?:microfinance\s+bank|mfb)|teamapt", head_lower):
            return True

        # The Moniepoint statement header block.
        if (
            "account statement" in head_lower
            and "account summary" in head_lower
            and "business name" in head_lower
            and ("total debits" in head_lower or "total credits" in head_lower)
        ):
            return True

        # Moniepoint ledger fingerprints: terminal ids (``2MPT8f877``) and reference
        # shapes. A single hit only means the account transacted *with* Moniepoint —
        # other banks' statements carry one in a narration — so require several.
        return len(_LEDGER_FINGERPRINT.findall(head)) >= 3

    # ----------------------------------------------------------------- summary

    def extract_summary(self, text: str) -> TransactionSummary | None:
        head = self._normalize_text(text)[:6000]

        account_name = self._labelled_value(head, r"Business\s+Name", r"[A-Za-z0-9'’,\.\-/&\s]+")
        account_number = self._labelled_value(head, r"Account\s+Number", r"\d{10}")
        # The trailing boundary matters: the wide export leaves Currency blank, and
        # without it the next label ("Total Debits") is read as the currency "Tot".
        currency = self._labelled_value(head, r"Currency", r"[A-Za-z]{3}\b")

        opening_balance = self._labelled_amount(head, r"Opening\s*(?:\n\s*)?Balance")
        closing_balance = self._labelled_amount(head, r"Closing\s*(?:\n\s*)?Balance")
        total_debit = self._labelled_amount(head, r"Total\s*(?:\n\s*)?Debits?")
        total_credit = self._labelled_amount(head, r"Total\s*(?:\n\s*)?Credits?")

        if not any([account_name, account_number, opening_balance, closing_balance,
                    total_debit, total_credit]):
            return super().extract_summary(text)

        return TransactionSummary(
            account_name=account_name,
            account_number=account_number,
            account_type="Business Account",
            currency=(currency or "NGN").upper(),
            open_balance=opening_balance,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_debit=total_debit,
            total_credit=total_credit,
        )

    def _labelled_value(self, head: str, label: str, value: str) -> str | None:
        """Read a labelled field laid out either inline or on the following line."""
        m = re.search(
            rf"(?:\*\*)?{label}(?:\*\*)?\s*[:|]?\s*(?:\*\*)?\s*({value})",
            head,
            re.IGNORECASE,
        )
        if not m:
            return None
        val = m.group(1).strip().strip("*|,: ").strip()
        # An inline label layout can run the value into the next label.
        val = re.split(
            r"\s*(?:Account\s+Number|Currency|Address|Date|Opening|Closing|Total)\b",
            val,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        return val or None

    def _labelled_amount(self, head: str, label: str) -> str | None:
        m = re.search(
            rf"(?:\*\*)?{label}(?:\*\*)?\s*[:|]*\s*(?:\|+\s*)?(?:₦\s*)?({_AMOUNT})",
            head,
            re.IGNORECASE,
        )
        return self._clean_number(m.group(1)) if m else None

    # ---------------------------------------------------------------- extract

    def extract(
        self,
        text: str,
        *,
        source_name: str | None = None,
        only_bills: bool = False,
    ) -> tuple[str, list[TransactionRecord]]:
        normalized = self._normalize_text(text)
        if not normalized:
            return "", []

        source = source_name or "moniepoint_statement"
        raw_lines = normalized.split("\n")
        lines = [ln.strip() for ln in raw_lines]

        # The wide transaction export is a different table entirely; it is recognised by
        # its own header line rather than by the six column ledger's date blocks. It is
        # given the unstripped lines so that trailing empty cells still count.
        wide = self._parse_wide_rows(raw_lines)
        if wide:
            records = [self._to_record(row, source) for row in wide]
            if only_bills:
                records = [r for r in records if self._is_bill_transaction(r)]
            return normalized, records

        rows = self._parse_blocks(lines)
        # Recover any row that only survives as a single flat line (pasted text, OCR,
        # or a markdown rendering). Keyed on the reference so nothing is counted twice.
        seen = {r["reference"] for r in rows if r.get("reference")}
        for row in self._parse_flat_rows(lines):
            ref = row.get("reference")
            if ref and ref in seen:
                continue
            if ref:
                seen.add(ref)
            rows.append(row)

        records = [self._to_record(row, source) for row in rows]

        if only_bills:
            records = [r for r in records if self._is_bill_transaction(r)]

        return normalized, records

    # ------------------------------------------------------------- wide parser

    def _parse_wide_rows(self, lines: list[str]) -> list[dict[str, Any]]:
        """Parse Moniepoint's wide transaction export.

        That export carries nineteen columns and leaves the optional ones blank, so a
        value's column is the only thing identifying it — ``Settlement Debit`` and
        ``Settlement Credit`` cannot be told apart from the text alone. Ingestion
        rebuilds the table from the page geometry (see
        ``services.pdf_inspector_service._layout_table_from_pdf``) and this reads the
        cells back by name.

        ``Settlement Debit`` / ``Settlement Credit`` are the amounts that actually moved
        the balance: they already include ``Charge``, so a row's ``Transaction Amount``
        plus its charge is the settlement figure, and Balance After follows from them.
        """
        columns = self._wide_columns(lines)
        if not columns:
            return []

        rows: list[dict[str, Any]] = []
        for line in lines:
            if _CELL not in line:
                continue
            cells = [c.strip() for c in line.split(_CELL)]
            if len(cells) != len(columns):
                continue
            values = dict(zip(columns, cells))

            # The Date cell wraps over four physical lines ("2025-" / "03-" / "06T06:" /
            # "40"), so the rebuilt cell carries the joining spaces.
            stamp = re.sub(r"\s+", "", values.get("date", ""))
            day = re.match(r"(\d{4}-\d{2}-\d{2})", stamp)
            if not day:
                continue

            debit = self._positive_amount(values.get("settlement debit"))
            credit = self._positive_amount(values.get("settlement credit"))
            if debit is None and credit is None:
                continue

            narration = " ".join(filter(None, [
                values.get("narration", ""),
                values.get("transaction type", ""),
                values.get("beneficiary", ""),
                values.get("source", ""),
            ])).strip()

            rows.append({
                "date": day.group(1),
                "time": self._wide_time(stamp),
                "narration": narration or values.get("transaction ref", ""),
                "reference": values.get("transaction ref") or None,
                "debit": values.get("settlement debit", ""),
                "credit": values.get("settlement credit", ""),
                "balance": values.get("balance after", ""),
                "raw": " | ".join([stamp, *cells[1:]]),
            })
        return rows

    def _wide_columns(self, lines: list[str]) -> list[str]:
        """Read the wide export's column order from the rendering's own header line."""
        for line in lines:
            cells = [c.strip().lower() for c in line.split(_CELL)]
            if "settlement debit" in cells and "settlement credit" in cells:
                return cells
        return []

    def _wide_time(self, stamp: str) -> str | None:
        m = re.search(r"T(\d{1,2}(?::\d{2}){0,2})$", stamp)
        if not m:
            return None
        parts = m.group(1).split(":")
        return ":".join([f"{int(parts[0]):02d}", *parts[1:]]) if parts else None

    # ------------------------------------------------------------ block parser

    def _parse_blocks(self, lines: list[str]) -> list[dict[str, Any]]:
        """Parse the columnar layout, where each transaction spans several lines."""
        anchors = [i for i, ln in enumerate(lines) if _DATE_ANCHOR.match(ln)]
        rows: list[dict[str, Any]] = []

        for pos, start in enumerate(anchors):
            end = anchors[pos + 1] if pos + 1 < len(anchors) else len(lines)
            block = [ln for ln in lines[start:end] if ln and not ln.startswith("|---")]
            row = self._row_from_block(block)
            if row:
                rows.append(row)

        return rows

    def _row_from_block(self, block: list[str]) -> dict[str, Any] | None:
        anchor = _DATE_ANCHOR.match(block[0])
        if not anchor:
            return None
        day, hour = anchor.group(1), anchor.group(2)

        amounts_at = self._find_amount_triple(block)
        if amounts_at is None:
            return None

        debit_raw, credit_raw, balance_raw = block[amounts_at:amounts_at + 3]
        # A row split by a page break continues *after* its amounts.
        body = [ln for ln in block[1:amounts_at] + block[amounts_at + 3:]
                if ln.strip("*").strip().lower() not in _HEADER_LINES]

        minute: str | None = None
        cells: list[str] = []
        for line in body:
            if minute is None and _TIME_PART.match(line):
                minute = _TIME_PART.match(line).group(1)
                continue
            cells.append(line.strip("|").strip())

        cells = [c for c in cells if c]
        reference = self._pick_reference(cells)
        narration_cells = cells[:-1] if (reference and cells and cells[-1] == reference) else cells
        narration = " ".join(narration_cells).strip() or reference or ""

        return {
            "date": day,
            "time": self._build_time(hour, minute),
            "narration": narration,
            "reference": reference,
            "debit": debit_raw,
            "credit": credit_raw,
            "balance": balance_raw,
            "raw": " ".join(block),
        }

    def _find_amount_triple(self, block: list[str]) -> int | None:
        """Index of the Debit / Credit / Balance run inside a transaction block."""
        for i in range(1, max(len(block) - 2, 1)):
            if all(_AMOUNT_LINE.match(block[i + off]) for off in range(3)):
                return i
        return None

    def _pick_reference(self, cells: Iterable[str]) -> str | None:
        """The Reference cell: a whitespace-free ledger token, last before the amounts."""
        candidates = [c for c in cells if c and not re.search(r"\s", c)]
        for cell in reversed(candidates):
            if _REFERENCE_TOKEN.search(cell) or "_RVSL" in cell:
                return cell
        return candidates[-1] if candidates else None

    def _build_time(self, hour: str, minute: str | None) -> str | None:
        """Rejoin the Date cell's two halves, e.g. ``09:`` + ``38:48`` -> ``09:38:48``.

        Returns None rather than guessing when the minute half is absent.
        """
        if not minute:
            return None
        return f"{int(hour):02d}:{minute}"

    # ------------------------------------------------------------- flat parser

    def _parse_flat_rows(self, lines: list[str]) -> list[dict[str, Any]]:
        """Parse rows that arrive collapsed onto a single line."""
        rows: list[dict[str, Any]] = []
        for line in lines:
            candidate = line.strip()
            if not candidate or candidate.startswith("|---"):
                continue
            if candidate.startswith("|"):
                candidate = " ".join(c.strip() for c in candidate.strip("|").split("|") if c.strip())
            m = _FLAT_ROW.match(candidate)
            if not m:
                continue

            day, time_part, middle, debit_raw, credit_raw, balance_raw = m.groups()
            cells = [c for c in re.split(r"\s{2,}", middle.strip()) if c.strip()] or [middle.strip()]
            reference = self._pick_reference(re.split(r"\s+", middle.strip()))
            narration = " ".join(cells).strip()

            rows.append({
                "date": day,
                "time": time_part,
                "narration": narration,
                "reference": reference,
                "debit": debit_raw,
                "credit": credit_raw,
                "balance": balance_raw,
                "raw": line.strip(),
            })
        return rows

    # ---------------------------------------------------------------- records

    def _to_record(self, row: dict[str, Any], source: str) -> TransactionRecord:
        debit = self._positive_amount(row["debit"])
        credit = self._positive_amount(row["credit"])
        narration = row["narration"]

        if debit and not credit:
            ttype, debit_val, credit_val = "debit", debit, None
        elif credit and not debit:
            ttype, debit_val, credit_val = "credit", None, credit
        else:
            # Both columns zero (or both populated): fall back to the narration.
            ttype, debit_val, credit_val = classify_transaction_type(
                narration, debit or credit
            )

        days_from_today, within_3_months = self._recency(row["date"])

        meta: dict[str, Any] = {
            "amount": debit_val or credit_val,
            "debit": debit_val,
            "credit": credit_val,
            "transaction_type": ttype,
            "date": row["date"],
            "time": row.get("time"),
            "days_from_today": days_from_today,
            "is_within_3_months": within_3_months,
            "category": categorise_transaction(narration),
            "transaction_number": row.get("reference"),
            "balance": self._signed_number(row.get("balance")),
        }
        return self._build_record(row.get("raw") or narration, meta, source)

    def _signed_number(self, raw: str | None) -> str | None:
        """Clean a number while keeping its sign.

        The shared ``_clean_number`` deliberately drops the sign because debit and
        credit columns carry magnitudes, but an overdrawn running balance is
        genuinely negative and must stay that way.
        """
        cleaned = self._clean_number(raw)
        if cleaned is None:
            return None
        return f"-{cleaned}" if (raw or "").strip().startswith("-") else cleaned

    def _positive_amount(self, raw: str | None) -> str | None:
        cleaned = self._clean_number(raw)
        if cleaned is None:
            return None
        return cleaned if float(cleaned) != 0.0 else None

    def _recency(self, day: str | None) -> tuple[int | None, bool | None]:
        if not day:
            return None, None
        try:
            return compute_date_recency(date_cls.fromisoformat(day))
        except Exception:
            return None, None
