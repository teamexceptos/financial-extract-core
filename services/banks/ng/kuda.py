"""Kuda Microfinance Bank statement extractor.

Kuda statements are laid out as a seven column table::

    Date/Time | Money In | Money out | Category | To / From | Description | Balance

Debit and credit are two *separate* columns, so a transaction's direction is carried by
which column its amount occupies — not by any word in the row. Reading direction from the
row text is not merely imprecise, it is wrong: a reversal is labelled category
``outward transfer`` while its amount is paid into ``Money In``.

These PDFs expose no plain text at all and their markdown rendering merges the two money
columns, so the columns are recovered from page geometry during ingestion (see
``services.pdf_inspector_service._layout_table_from_pdf``). This extractor consumes that
canonical rendering, addressing cells by their header name.
"""

from __future__ import annotations

import re
from datetime import date as date_cls
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.date_recency import compute_date_recency
from utils.receipt_metadata import categorise_transaction

_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{2,4})\b")
_TIME = re.compile(r"\b(\d{2}:\d{2}:\d{2})\b")
_AMOUNT = re.compile(r"-?\d{1,3}(?:,\d{3})*\.\d{2}|-?\d+\.\d{2}")

_HEADER_CELLS = ("date/time", "money in", "money out", "category", "balance")
# Ingestion rebuilds the table with tab-delimited cells (see LAYOUT_TABLE_DELIMITER).
_CELL = "\t"


class KudaExtractor(BaseBankExtractor):
    """Extractor for Kuda Microfinance Bank statements."""

    bank_name: str = "Kuda"
    bank_code: str = "kuda"
    aliases: tuple[str, ...] = ("kuda_bank", "kudamfb", "kuda_mfb")

    # ------------------------------------------------------------------ detect

    def detect(self, text: str) -> bool:
        head = text[:8000]
        head_lower = head.lower()

        # Markers from Kuda's own statement furniture that a counterparty field can never
        # carry. Neither a bare "kuda" nor even "Kuda MF Bank" is enough: Kuda statements
        # name counterparties as ".../Kuda", and other banks' statements record Kuda as a
        # transfer's source institution ("KUDA MICROFINANCE BANK").
        if re.search(r"rc796975|kudabank|kuda technologies", head_lower):
            return True

        # The Kuda transaction table header.
        return "money in" in head_lower and "money out" in head_lower and "date/time" in head_lower

    # ----------------------------------------------------------------- summary

    def extract_summary(self, text: str) -> TransactionSummary | None:
        head = self._normalize_text(text)[:4000]

        account_name = self._labelled(head, "Account Name")
        account_number = self._labelled(head, "Account")
        if account_number and not re.fullmatch(r"\d{10}", account_number):
            account_number = None

        opening_balance = self._clean_number(self._labelled(head, "Opening Balance"))
        closing_balance = self._clean_number(self._labelled(head, "Closing Balance"))
        # Kuda labels the period totals "Money in" / "Money out".
        total_credit = self._clean_number(self._labelled(head, "Money in"))
        total_debit = self._clean_number(self._labelled(head, "Money out"))

        if not any([account_name, account_number, opening_balance, closing_balance,
                    total_debit, total_credit]):
            return super().extract_summary(text)

        return TransactionSummary(
            account_name=account_name,
            account_number=account_number,
            account_type="Savings",
            currency="NGN",
            open_balance=opening_balance,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_debit=total_debit,
            total_credit=total_credit,
        )

    def _labelled(self, head: str, label: str) -> str | None:
        m = re.search(rf"(?m)^{re.escape(label)}\s*:[ \t]*(.+?)[ \t]*$", head, re.IGNORECASE)
        if not m:
            return None
        return m.group(1).strip().strip("|").strip() or None

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

        source = source_name or "kuda_statement"
        lines = normalized.split("\n")
        columns = self._column_names(lines)
        records: list[TransactionRecord] = []

        for line in lines:
            row = line.rstrip("\n")
            if not row.strip() or _CELL not in row:
                continue
            cells = [c.strip() for c in row.split(_CELL)]
            if not _DATE.search(cells[0]):
                continue

            values = dict(zip(columns, cells)) if columns else {}
            record = self._to_record(values, cells, columns, source)
            if record is not None:
                records.append(record)

        if only_bills:
            records = [r for r in records if self._is_bill_transaction(r)]

        return normalized, records

    def _column_names(self, lines: list[str]) -> list[str]:
        """Read the column order from the rendering's own header line."""
        for line in lines:
            cells = [c.strip().lower() for c in line.split(_CELL)]
            if sum(1 for c in cells if c in _HEADER_CELLS) >= 4:
                return cells
        return []

    def _to_record(
        self,
        values: dict[str, str],
        cells: list[str],
        columns: list[str],
        source: str,
    ) -> TransactionRecord | None:
        day = _DATE.search(cells[0])
        if not day:
            return None
        date_val = self._parse_dmy_date(day.group(1))
        time_m = _TIME.search(cells[0])

        credit = self._amount(values.get("money in"))
        debit = self._amount(values.get("money out"))
        if credit is None and debit is None:
            return None
        if credit is not None and debit is not None:
            # Never guess: a row carries exactly one of the two money columns.
            return None

        category_col = values.get("category", "")
        to_from = values.get("to / from", "")
        description = values.get("description", "")
        balance = self._signed_number(values.get("balance"))

        # Kuda's own Category column is a channel label; the app's taxonomy comes from
        # the transaction text.
        category = categorise_transaction(" ".join(filter(None, [category_col, to_from, description])))
        days_from_today, within_3_months = self._recency(date_val)
        raw_row = " | ".join(values.get(name, "") for name in columns) if columns \
            else " | ".join(cells)   # readable rendering of the statement's own row

        meta: dict[str, Any] = {
            "amount": debit or credit,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "transaction_type": "debit" if debit else "credit",
            "date": date_val,
            "time": time_m.group(1) if time_m else None,
            "days_from_today": days_from_today,
            "is_within_3_months": within_3_months,
            "category": category,
            # Kuda statements print no per-transaction reference.
            "transaction_number": None,
        }
        return self._build_record(raw_row, meta, source)

    # ----------------------------------------------------------------- helpers

    def _amount(self, raw: str | None) -> str | None:
        if not raw:
            return None
        m = _AMOUNT.search(raw.replace("₦", ""))
        if not m:
            return None
        cleaned = self._clean_number(m.group(0))
        if cleaned is None or float(cleaned) == 0.0:
            return None
        return cleaned

    def _signed_number(self, raw: str | None) -> str | None:
        """Clean a number while keeping its sign, so an overdrawn balance stays negative."""
        if not raw:
            return None
        m = _AMOUNT.search(raw.replace("₦", ""))
        if not m:
            return None
        cleaned = self._clean_number(m.group(0))
        if cleaned is None:
            return None
        return f"-{cleaned}" if m.group(0).strip().startswith("-") else cleaned

    def _recency(self, day: str | None) -> tuple[int | None, bool | None]:
        if not day:
            return None, None
        try:
            return compute_date_recency(date_cls.fromisoformat(day))
        except Exception:
            return None, None

    def _parse_dmy_date(self, val: str) -> str | None:
        try:
            from dateutil import parser as dp
            return dp.parse(val, fuzzy=True, dayfirst=True).date().isoformat()
        except Exception:
            return val
