"""GTBank (Guaranty Trust Bank) statement extractor."""

from __future__ import annotations

import re
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.receipt_metadata import categorise_transaction, classify_transaction_type


class GTBankExtractor(BaseBankExtractor):
    """Extractor for GTBank / GTCO customer statements."""

    bank_name: str = "Guaranty Trust Bank"
    bank_code: str = "gtbank"
    aliases: tuple[str, ...] = ("gtb", "gtco", "guaranty_trust", "guaranty_trust_bank")

    def detect(self, text: str) -> bool:
        # Restrict to first 1000 chars (pure header) to avoid matching
        # "Guaranty Trust Bank" appearing inside other banks' transaction narrations.
        head = text[:1000].lower()
        return (
            ("customer statement" in head and ("usable balance" in head or "branch name" in head or "internal reference" in head))
            or "guaranty trust bank" in head
            or "gtbank" in head
            or "gtco" in head
        )

    def extract_summary(self, text: str) -> TransactionSummary | None:
        head = text[:5000]
        head_lower = head.lower()

        account_name: str | None = None
        account_number: str | None = None
        account_type: str | None = "Savings"
        currency: str | None = "NGN"
        opening_balance: str | None = None
        closing_balance: str | None = None
        total_debit: str | None = None
        total_credit: str | None = None

        gt_name = re.search(r"CUSTOMER\s+STATEMENT\s*[\r\n]+\s*([A-Za-z\s]+?)(?=\n|\|)", head, re.IGNORECASE)
        if gt_name:
            account_name = gt_name.group(1).strip()

        gt_acc = re.search(r"Account\s+No\s*\|\s*(\d+)\s*\|", head, re.IGNORECASE)
        if gt_acc:
            account_number = gt_acc.group(1).strip()

        gt_type = re.search(r"Account\s+Type\s*\|\s*([A-Za-z\s]+?)\s*\|", head, re.IGNORECASE)
        if gt_type:
            raw_t = gt_type.group(1).strip()
            account_type = "Savings" if raw_t.upper() == "SAVINGS" else raw_t.title()

        gt_deb = re.search(r"Total\s+Debit\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if gt_deb:
            total_debit = self._clean_number(gt_deb.group(1))

        gt_cred = re.search(r"Total\s+Credit\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if gt_cred:
            total_credit = self._clean_number(gt_cred.group(1))

        gt_open = re.search(r"Opening\s+Balance\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if gt_open:
            opening_balance = self._clean_number(gt_open.group(1))

        gt_close = re.search(r"Closing\s+Balance\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if gt_close:
            closing_balance = self._clean_number(gt_close.group(1))

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

        source = source_name or "gtbank_statement"
        records: list[TransactionRecord] = []

        for line in normalized.split("\n"):
            line_str = line.strip()
            if not line_str or line_str.startswith("|---"):
                continue

            lowered = line_str.lower()
            if any(t in lowered for t in [
                "statement period", "print. date", "branch name", "account no", "internal reference",
                "account type", "currency", "total debit", "total credit", "closing balance",
                "usable balance", "opening balance", "customer statement", "this is a computer generated",
                "trans. date|value. date",
            ]):
                continue

            if line_str.startswith("|"):
                cols = [c.strip() for c in line_str.split("|")[1:-1]]
                if len(cols) >= 6:
                    date_val = self._parse_date(cols[0])
                    ref = cols[2] if len(cols) > 2 else ""
                    debit_str = cols[3] if len(cols) > 3 else ""
                    credit_str = cols[4] if len(cols) > 4 else ""
                    narration = cols[7] if len(cols) > 7 else (cols[-1] if len(cols) > 5 else "")

                    debit_val = self._clean_number(debit_str)
                    credit_val = self._clean_number(credit_str)

                    ttype: str | None = None
                    amount_val: str | None = None
                    if debit_val:
                        ttype, amount_val = "debit", debit_val
                    elif credit_val:
                        ttype, amount_val = "credit", credit_val
                    else:
                        ttype, debit_val, credit_val = classify_transaction_type(narration)
                        amount_val = debit_val or credit_val

                    cat = categorise_transaction(narration)
                    meta: dict[str, Any] = {
                        "amount": amount_val,
                        "debit": debit_val,
                        "credit": credit_val,
                        "transaction_type": ttype,
                        "date": date_val,
                        "category": cat,
                        "transaction_number": ref or None,
                    }
                    records.append(self._build_record(line_str, meta, source))

        if only_bills:
            records = [r for r in records if self._is_bill_transaction(r)]

        return normalized, records

    def _parse_date(self, val: str) -> str | None:
        m = re.search(r"(\d{1,2}-[A-Za-z]{3}-\d{4})", val)
        if m:
            try:
                from dateutil import parser as dp
                return dp.parse(m.group(1), fuzzy=True, dayfirst=True).date().isoformat()
            except Exception:
                return m.group(1)
        return None
