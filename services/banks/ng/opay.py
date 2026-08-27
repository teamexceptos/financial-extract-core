"""OPay / Blue Ridge Microfinance Bank statement extractor."""

from __future__ import annotations

import re
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.receipt_metadata import categorise_transaction, classify_transaction_type


class OPayExtractor(BaseBankExtractor):
    """Extractor for OPay / Blue Ridge Microfinance Bank statements."""

    bank_name: str = "OPay"
    bank_code: str = "opay"
    aliases: tuple[str, ...] = ("opay_ng", "blueridge", "blue_ridge", "opay_bank")

    def detect(self, text: str) -> bool:
        head = text[:3000].lower()
        return (
            "blue ridge microfinance" in head
            or "owealth" in head
            or "summary-wallet balance" in head
            or ("opay" in head and ("wallet balance" in head or "current balance" in head))
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

        opay_name = re.search(r"Account\s+Name\s+([A-Za-z\s]+?)\s+Address", head, re.IGNORECASE)
        if opay_name:
            account_name = opay_name.group(1).strip()

        opay_nums = re.search(
            r"Account\s+Number\s+Total\s+Credit\s+Total\s+Debit\s+(\d{10})\s+₦?([0-9,.]+)\s+₦?([0-9,.]+)",
            head,
            re.IGNORECASE,
        )
        if opay_nums:
            account_number = opay_nums.group(1).strip()
            total_credit = self._clean_number(opay_nums.group(2))
            total_debit = self._clean_number(opay_nums.group(3))

        opay_type = re.search(r"Account\s+Type\s+Credit\s+Count\s+Debit\s+Count\s+([A-Za-z\s]+?)\s+\d+", head, re.IGNORECASE)
        if opay_type:
            account_type = opay_type.group(1).strip()

        opay_bal = re.search(
            r"Summary-Wallet\s+Balance\s+Opening\s+Balance\s+Closing\s+Balance.*?₦([0-9,.]+)\s+₦([0-9,.]+)",
            head,
            re.IGNORECASE,
        )
        if opay_bal:
            opening_balance = self._clean_number(opay_bal.group(1))
            closing_balance = self._clean_number(opay_bal.group(2))
        else:
            opay_curr = re.search(r"Current\s+Balance[^\n]*?₦?([0-9,.]+)", head, re.IGNORECASE)
            if opay_curr:
                closing_balance = self._clean_number(opay_curr.group(1))
                opening_balance = "0.00"

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

        source = source_name or "opay_statement"
        records: list[TransactionRecord] = []

        for line in normalized.split("\n"):
            line_str = line.strip()
            if not line_str or line_str.startswith("|---"):
                continue

            lowered = line_str.lower()
            if any(h in lowered for h in ["account statement", "summary-wallet", "trans. date|value date", "opening balance", "current balance"]):
                continue

            # 1. Pipe-separated table rows
            if line_str.startswith("|"):
                cols = [c.strip() for c in line_str.split("|")[1:-1]]
                if len(cols) >= 4:
                    date_val = self._parse_date(cols[0])
                    narration = cols[2] if len(cols) > 2 else cols[0]
                    amount_col = cols[3] if len(cols) > 3 else ""
                    ttype, debit, credit = self._parse_amount(amount_col, narration)
                    ref = cols[6] if len(cols) > 6 else (cols[-1] if len(cols) > 4 else None)

                    days_from_today, within_3_months = self._recency(date_val)
                    cat = categorise_transaction(narration)
                    meta: dict[str, Any] = {
                        "amount": debit or credit,
                        "debit": debit,
                        "credit": credit,
                        "transaction_type": ttype,
                        "date": date_val,
                        "days_from_today": days_from_today,
                        "is_within_3_months": within_3_months,
                        "category": cat,
                        "transaction_number": ref,
                    }
                    records.append(self._build_record(line_str, meta, source))
                    continue

            # 2. Space-separated rows
            m = re.match(
                r"^(\d{2}\s+[A-Za-z]{3}\s+\d{4})\s+(\d{2}\s+[A-Za-z]{3}\s+\d{4})\s+(.+?)\s+([+-]?[0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+(.+)$",
                line_str,
            )
            if m:
                date_val = self._parse_date(m.group(1))
                narration = m.group(3).strip()
                amount_str = m.group(4).strip()
                ttype, debit, credit = self._parse_amount(amount_str, narration)
                rest = m.group(6).strip()
                ref_match = re.search(r"(\d{15,40})", rest)
                ref = ref_match.group(1) if ref_match else None

                days_from_today, within_3_months = self._recency(date_val)
                cat = categorise_transaction(narration)
                meta = {
                    "amount": debit or credit,
                    "debit": debit,
                    "credit": credit,
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

    def _parse_amount(self, val: str, narration: str) -> tuple[str | None, str | None, str | None]:
        val_clean = val.strip().replace(",", "").replace("₦", "").strip()
        num = self._clean_number(val_clean)
        if val.startswith("+"):
            return "credit", None, num
        if val.startswith("-"):
            return "debit", num, None
        return classify_transaction_type(narration, num)
