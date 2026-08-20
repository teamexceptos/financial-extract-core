"""VFD Microfinance Bank statement extractor — pipe-delimited layout."""

from __future__ import annotations

import re
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.receipt_metadata import categorise_transaction, classify_transaction_type


class VFDExtractor(BaseBankExtractor):
    """Extractor for VFD Microfinance Bank pipe-delimited / table statements."""

    bank_name: str = "VFD Microfinance Bank"
    bank_code: str = "vfd"
    aliases: tuple[str, ...] = ("vfd_bank", "vfd_mfb", "vfd_microfinance")

    def detect(self, text: str) -> bool:
        head = text[:3000].lower()
        return (
            "vfd microfinance bank" in head
            or ("nuban product name" in head and "total amount on hold" in head)
            or ("vfd group" in head and "statement" in head)
        )

    def extract_summary(self, text: str) -> TransactionSummary | None:
        head = text[:5000]
        head_lower = head.lower()

        account_name: str | None = None
        account_number: str | None = None
        account_type: str | None = "Corporate Current Account"
        currency: str | None = "NGN"
        opening_balance: str | None = None
        closing_balance: str | None = None
        total_debit: str | None = None
        total_credit: str | None = None

        vfd_name = re.search(r"(?:##\s*)?([A-Za-z0-9\s,\.\-&]+?)\s*[\r\n]+(?:###\s*)?(?:<u>\s*)?STATEMENT", head, re.IGNORECASE)
        if vfd_name:
            account_name = vfd_name.group(1).strip()

        vfd_acc = re.search(r"(?:NUBAN\s+Product\s+Name|Account\s+No)\s*\|\s*(\d{10})\s*\|", head, re.IGNORECASE)
        if vfd_acc:
            account_number = vfd_acc.group(1).strip()

        if "corporate current account" in head_lower:
            account_type = "Corporate Current Account"
        elif "savings" in head_lower:
            account_type = "Savings"

        vfd_open = re.search(r"Opening\s+Balance\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if vfd_open:
            opening_balance = self._clean_number(vfd_open.group(1))

        vfd_close = re.search(r"Closing\s+Balance\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if vfd_close:
            closing_balance = self._clean_number(vfd_close.group(1))

        vfd_deb = re.search(r"Total\s+Debit(?:\s+Amount)?\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if vfd_deb:
            total_debit = self._clean_number(vfd_deb.group(1))

        vfd_cred = re.search(r"Total\s+Credit(?:\s+Amount)?\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if vfd_cred:
            total_credit = self._clean_number(vfd_cred.group(1))

        if not any([account_name, account_number, account_type, opening_balance, closing_balance, total_debit, total_credit]):
            return super().extract_summary(text)

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

        source = source_name or "vfd_statement"
        records: list[TransactionRecord] = []

        for line in normalized.split("\n"):
            line_str = line.strip()
            if not line_str or line_str.startswith("|---"):
                continue

            lowered = line_str.lower()
            if any(t in lowered for t in [
                "statement period", "nuban product name", "opening balance", "total debit amount",
                "total credit amount", "closing balance", "currency",
                "transaction date|debit|credit|balance",
                "vfd microfinance bank",
            ]):
                continue

            if line_str.startswith("|"):
                cols = [c.strip() for c in line_str.split("|")[1:-1]]
                if len(cols) >= 4:
                    date_val = self._parse_iso_date(cols[0])
                    if not date_val:
                        continue

                    deb_str = cols[1] if len(cols) > 1 else ""
                    cred_str = cols[2] if len(cols) > 2 else ""

                    deb_val = self._clean_number(deb_str) if deb_str not in ("", "0.00") else None
                    cred_val = self._clean_number(cred_str) if cred_str not in ("", "0.00") else None

                    narration = " ".join(c for c in cols[4:] if c) if len(cols) > 4 else cols[3]
                    if not narration:
                        narration = cols[3]

                    ttype: str | None = None
                    amount_val: str | None = None
                    if deb_val:
                        ttype, amount_val = "debit", deb_val
                    elif cred_val:
                        ttype, amount_val = "credit", cred_val
                    else:
                        ttype, deb_val, cred_val = classify_transaction_type(narration)
                        amount_val = deb_val or cred_val

                    cat = categorise_transaction(narration)
                    meta: dict[str, Any] = {
                        "amount": amount_val,
                        "debit": deb_val,
                        "credit": cred_val,
                        "transaction_type": ttype,
                        "date": date_val,
                        "category": cat,
                        "transaction_number": None,
                    }
                    records.append(self._build_record(line_str, meta, source))

        if only_bills:
            records = [r for r in records if self._is_bill_transaction(r)]

        return normalized, records

    def _parse_iso_date(self, val: str) -> str | None:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", val)
        return m.group(1) if m else None
