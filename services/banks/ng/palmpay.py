"""PalmPay statement extractor."""

from __future__ import annotations

import re
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.receipt_metadata import categorise_transaction, classify_transaction_type


class PalmPayExtractor(BaseBankExtractor):
    """Extractor for PalmPay Limited account statements."""

    bank_name: str = "PalmPay"
    bank_code: str = "palmpay"
    aliases: tuple[str, ...] = ("palm_pay", "palmpay_ng")

    def detect(self, text: str) -> bool:
        head = text[:3000].lower()
        return (
            "palmpay limited" in head
            or ("palmpay" in head and ("wallet account" in head or "debit count" in head))
            or "trans. time|value date|description|debit(₦)|credit(₦)|balance after(₦)" in head
        )

    def extract_summary(self, text: str) -> TransactionSummary | None:
        head = text[:5000]
        head_lower = head.lower()

        account_name: str | None = None
        account_number: str | None = None
        account_type: str | None = "Wallet Account"
        currency: str | None = "NGN"
        opening_balance: str | None = None
        closing_balance: str | None = None
        total_debit: str | None = None
        total_credit: str | None = None

        pp_name = re.search(r"Account\s+Name\s+([A-Za-z\s]+?)\s+(?:Wallet\s+Account|Opening\s+Balance)", head, re.IGNORECASE)
        if pp_name:
            account_name = pp_name.group(1).strip()

        pp_acc = re.search(r"Account\s+Number\s+(\d{10})", head, re.IGNORECASE)
        if pp_acc:
            account_number = pp_acc.group(1).strip()

        pp_open = re.search(r"Opening\s+Balance\s+₦?([0-9,.]+)", head, re.IGNORECASE)
        if pp_open:
            opening_balance = self._clean_number(pp_open.group(1))

        pp_close = re.search(r"Closing\s+Balance\s+₦?([0-9,.]+)", head, re.IGNORECASE)
        if pp_close:
            closing_balance = self._clean_number(pp_close.group(1))

        pp_deb = re.search(r"Total\s+Debit\s+₦?([0-9,.]+)", head, re.IGNORECASE)
        if pp_deb:
            total_debit = self._clean_number(pp_deb.group(1))

        pp_cred = re.search(r"Total\s+Credit\s+₦?([0-9,.]+)", head, re.IGNORECASE)
        if pp_cred:
            total_credit = self._clean_number(pp_cred.group(1))

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

        source = source_name or "palmpay_statement"
        records: list[TransactionRecord] = []

        for line in normalized.split("\n"):
            line_str = line.strip()
            if not line_str or line_str.startswith("|---"):
                continue

            lowered = line_str.lower()
            if "account name" in lowered and "wallet account" in lowered:
                continue
            if "trans. time" in lowered and "value date" in lowered:
                continue

            if line_str.startswith("|"):
                cols = [c.strip() for c in line_str.split("|")[1:-1]]
                if len(cols) >= 5:
                    date_val = self._parse_date(cols[0])
                    narration = cols[2] if len(cols) > 2 else cols[0]
                    debit_str = cols[3] if len(cols) > 3 else ""
                    credit_str = cols[4] if len(cols) > 4 else ""

                    debit_val = self._clean_number(debit_str) if debit_str and debit_str != "--" else None
                    credit_val = self._clean_number(credit_str) if credit_str and credit_str != "--" else None

                    ttype: str | None = None
                    amount_val: str | None = None
                    if debit_val:
                        ttype, amount_val = "debit", debit_val
                    elif credit_val:
                        ttype, amount_val = "credit", credit_val
                    else:
                        ttype, debit_val, credit_val = classify_transaction_type(narration)
                        amount_val = debit_val or credit_val

                    ref = cols[7] if len(cols) > 7 else (cols[-1] if len(cols) > 5 else None)
                    cat = categorise_transaction(narration)

                    days_from_today, within_3_months = self._recency(date_val)
                    meta: dict[str, Any] = {
                        "amount": amount_val,
                        "debit": debit_val,
                        "credit": credit_val,
                        "transaction_type": ttype,
                        "date": date_val,
                        "days_from_today": days_from_today,
                        "is_within_3_months": within_3_months,
                        "category": cat,
                        "transaction_number": ref,
                    }
                    records.append(self._build_record(line_str, meta, source))

        if only_bills:
            records = [r for r in records if self._is_bill_transaction(r)]

        return normalized, records
