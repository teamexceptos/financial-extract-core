"""Base class and common utilities for Nigerian bank statement extractors."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date as _date_cls
from typing import Any

from models.transaction import TransactionMetadata, TransactionRecord, TransactionSummary
from utils.date_recency import compute_date_recency

BILL_CATEGORIES = {"Utility", "Cable TV", "Internet", "Cooking Gas", "Water", "Mobile", "Airtime", "Mobile Data"}


class BaseBankExtractor(ABC):
    """Abstract base class for individual bank extractors."""

    bank_name: str = "Unknown Bank"
    bank_code: str = "general"
    aliases: tuple[str, ...] = ()

    @abstractmethod
    def detect(self, text: str) -> bool:
        """Return True if the document text matches this bank's signature."""
        raise NotImplementedError

    @abstractmethod
    def extract(
        self,
        text: str,
        *,
        source_name: str | None = None,
        only_bills: bool = False,
    ) -> tuple[str, list[TransactionRecord]]:
        """Extract transactions from the bank statement text.

        Returns (normalized_text, list_of_transaction_records).
        """
        raise NotImplementedError

    def extract_summary(self, text: str) -> TransactionSummary | None:
        """Extract statement account summary (opening/closing balance, totals, etc.).

        Default generic implementation searching standard patterns.
        Subclasses should override with bank-specific regexes.
        """
        if not text:
            return None

        head = text[:5000]
        head_lower = head.lower()

        currency: str | None = None
        curr_m = re.search(
            r"(?:Currency|CURRENCY)\s*(?:\||:|\*\*)*\s*([A-Za-z\s]+?)(?=\s*(?:\n|\||\*\*|\bDate\b|\bAddress\b))",
            head,
            re.IGNORECASE,
        )
        if curr_m:
            raw_c = curr_m.group(1).strip().strip("*,#|").strip()
            currency = "NGN" if "naira" in raw_c.lower() or raw_c.upper() in ("NGN", "NAIRA") else raw_c.upper()
        elif "₦" in head or "naira" in head_lower:
            currency = "NGN"

        account_name: str | None = None
        name_m = re.search(
            r"(?:Account\s+Name|Customer\s+Name|Business\s+Name)\s*(?:\||:|\*\*)*\s*([A-Za-z\s,\.\-]+?)(?=\s+(?:Address|Wallet|Account|Period|Current|Opening|Debit|Credit|\n|\|))",
            head,
            re.IGNORECASE,
        )
        if name_m:
            account_name = name_m.group(1).strip().strip("*,#|").strip()

        account_number: str | None = None
        acc_m = re.search(
            r"(?:Account\s+Number|Account\s+No|NUBAN(?:\s+Product\s+Name)?)\s*(?:\||:|\*\*)*\s*(\d{10})",
            head,
            re.IGNORECASE,
        )
        if acc_m:
            account_number = acc_m.group(1).strip()

        account_type: str | None = None
        type_m = re.search(
            r"(?:Account\s+Type)\s*(?:\||:|\*\*)*\s*([A-Za-z\s]+?)(?=\s*(?:Credit|Debit|Count|Currency|Opening|Closing|\||\n))",
            head,
            re.IGNORECASE,
        )
        if type_m:
            raw_t = type_m.group(1).strip().strip("|*").strip()
            account_type = raw_t.title()
        elif "wallet account" in head_lower:
            account_type = "Wallet Account"
        elif "corporate current account" in head_lower:
            account_type = "Corporate Current Account"
        elif "savings" in head_lower:
            account_type = "Savings"
        elif "current" in head_lower:
            account_type = "Current"

        opening_balance: str | None = None
        open_m = re.search(
            r"(?:Opening\s+Balance|Opening)\s*(?:\||:|\*\*)*\s*(?:₦|\$)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)",
            head,
            re.IGNORECASE,
        )
        if open_m:
            opening_balance = self._clean_number(open_m.group(1))

        closing_balance: str | None = None
        close_m = re.search(
            r"(?:Closing\s+Balance|Closing)\s*(?:\||:|\*\*)*\s*(?:₦|\$)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)",
            head,
            re.IGNORECASE,
        )
        if close_m:
            closing_balance = self._clean_number(close_m.group(1))

        total_debit: str | None = None
        deb_m = re.search(
            r"(?:Total\s+Debit(?:s|\s+Amount)?)\s*(?:\||:|\*\*)*\s*(?:₦|\$)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)",
            head,
            re.IGNORECASE,
        )
        if deb_m:
            total_debit = self._clean_number(deb_m.group(1))

        total_credit: str | None = None
        cred_m = re.search(
            r"(?:Total\s+Credit(?:s|\s+Amount)?)\s*(?:\||:|\*\*)*\s*(?:₦|\$)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)",
            head,
            re.IGNORECASE,
        )
        if cred_m:
            total_credit = self._clean_number(cred_m.group(1))

        if not any([account_name, account_number, account_type, currency, opening_balance, closing_balance, total_debit, total_credit]):
            return None

        return TransactionSummary(
            account_name=account_name,
            account_number=account_number,
            account_type=account_type,
            currency=currency,
            open_balance=opening_balance,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_debit=total_debit,
            total_credit=total_credit,
        )

    def _normalize_text(self, text: str) -> str:
        text = text or ""
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text.strip()

    def _clean_number(self, val: str | None) -> str | None:
        if not val:
            return None
        cleaned = val.strip().replace(",", "").replace("₦", "").replace("$", "").strip()
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

    def _is_bill_transaction(self, tx: TransactionRecord) -> bool:
        return tx.metadata.category in BILL_CATEGORIES

    def _parse_date(self, val: str | None) -> str | None:
        if not val:
            return None
        m = re.search(
            r"(\d{4}-\d{2}-\d{2})T[\d:]+|"
            r"(\d{4}-\d{2}-\d{2})|"
            r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})|"
            r"(\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4})|"
            r"(\d{2}[-/]\d{2}[-/]\d{2,4})|"
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
            val,
        )
        raw = None
        if m:
            raw = next((g for g in m.groups() if g), None)
        if raw:
            try:
                from dateutil import parser as dp
                return dp.parse(raw, fuzzy=True, dayfirst=True).date().isoformat()
            except Exception:
                return raw
        return None

    def _recency(self, day: str | None) -> tuple[int | None, bool | None]:
        if not day:
            return None, None
        try:
            return compute_date_recency(_date_cls.fromisoformat(day))
        except Exception:
            return None, None

    def _build_record(
        self,
        raw_row: str,
        metadata: dict[str, Any],
        source: str,
    ) -> TransactionRecord:
        """Construct a TransactionRecord keeping the exact raw row as description."""
        return TransactionRecord(
            source=source,
            description=raw_row.strip(),
            metadata=TransactionMetadata(
                amount=metadata.get("amount"),
                debit=metadata.get("debit"),
                credit=metadata.get("credit"),
                balance=metadata.get("balance"),
                transaction_type=metadata.get("transaction_type"),
                date=metadata.get("date"),
                time=metadata.get("time"),
                days_from_today=metadata.get("days_from_today"),
                is_within_3_months=metadata.get("is_within_3_months"),
                category=metadata.get("category"),
                transaction_number=metadata.get("transaction_number") or metadata.get("receipt_number"),
            ),
        )
