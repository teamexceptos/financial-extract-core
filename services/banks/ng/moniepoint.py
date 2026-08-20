"""Moniepoint Microfinance Bank statement extractor."""

from __future__ import annotations

import re
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.receipt_metadata import categorise_transaction, classify_transaction_type


class MoniepointExtractor(BaseBankExtractor):
    """Extractor for Moniepoint MFB / TeamApt statements."""

    bank_name: str = "Moniepoint"
    bank_code: str = "moniepoint"
    aliases: tuple[str, ...] = ("monie_point", "teamapt")

    def detect(self, text: str) -> bool:
        head = text[:8000].lower()
        return (
            "moniepoint" in head
            or "teamapt" in head
            or "business name" in head
            or ("account summary" in head and ("total debits" in head or "opening balance" in head))
            or (
                "total debits" in head
                and "total credits" in head
                and "opening" in head
                and "closing balance" in head
                and re.search(r"\bcurrency\s*ngn\b", head) is not None
            )
        )

    def extract_summary(self, text: str) -> TransactionSummary | None:
        head = text[:5000]
        head_lower = head.lower()

        account_name: str | None = None
        account_number: str | None = None
        account_type: str | None = "Business Account"
        currency: str | None = "NGN"
        opening_balance: str | None = None
        closing_balance: str | None = None
        total_debit: str | None = None
        total_credit: str | None = None

        mp_name = re.search(r"(?:\*\*)?Business\s+Name(?:\*\*)?\s*([A-Za-z\s,\.\-]+?)(?:\s*(?:\*\*)?Account\s+Number(?:\*\*)?|\s*\n|\s*\|)", head, re.IGNORECASE)
        if mp_name:
            account_name = mp_name.group(1).strip()

        mp_acc = re.search(r"(?:\*\*)?Account\s+Number(?:\*\*)?\s*(\d{10})", head, re.IGNORECASE)
        if mp_acc:
            account_number = mp_acc.group(1).strip()

        mp_open = re.search(r"Opening(?: Balance)?\s*\|\s*(?:\|+)?\s*([0-9,.]+)", head, re.IGNORECASE)
        if mp_open:
            opening_balance = self._clean_number(mp_open.group(1))

        mp_close = re.search(r"Closing\s+Balance\s*\|\s*(?:\|+)?\s*([0-9,.]+)", head, re.IGNORECASE)
        if mp_close:
            closing_balance = self._clean_number(mp_close.group(1))

        mp_deb = re.search(r"Total\s+Debits?\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if mp_deb:
            total_debit = self._clean_number(mp_deb.group(1))

        mp_cred = re.search(r"Total\s+Credits?\s*\|\s*([0-9,.]+)\s*\|", head, re.IGNORECASE)
        if mp_cred:
            total_credit = self._clean_number(mp_cred.group(1))

        if not total_debit or not total_credit:
            mp_side = re.search(r"Total\s+Debits?\s+Total\s+Credits?\s*\|\s*([0-9,.]+)\s+([0-9,.]+)", head, re.IGNORECASE)
            if mp_side:
                total_debit = self._clean_number(mp_side.group(1))
                total_credit = self._clean_number(mp_side.group(2))

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

        source = source_name or "moniepoint_statement"
        records: list[TransactionRecord] = []

        for line in normalized.split("\n"):
            line_str = line.strip()
            if not line_str or line_str.startswith("|---"):
                continue

            lowered = line_str.lower()
            if any(t in lowered for t in [
                "account statement", "account summary", "business name", "opening balance",
                "closing balance", "total debit", "total credit", "currency ngn",
                "date narration reference debit credit balance",
            ]):
                continue

            # 1. Bold date timestamp rows: **2025-01-31T11:** Transfer from ... 0.00 120.00 120.00
            m_bold = re.search(
                r"^\*{0,2}(\d{4}-\d{2}-\d{2}T[\d:]+)\*{0,2}\s+(.+?)\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})$",
                line_str,
            )
            if m_bold:
                dt_raw = m_bold.group(1)
                narration = m_bold.group(2).strip()
                deb_str = m_bold.group(3).strip()
                cred_str = m_bold.group(4).strip()

                deb_val = self._clean_number(deb_str) if deb_str != "0.00" else None
                cred_val = self._clean_number(cred_str) if cred_str != "0.00" else None

                ttype, d, c = self._resolve_dc(deb_val, cred_val, narration)
                date_val = dt_raw.split("T")[0]
                cat = categorise_transaction(narration)

                ref_m = re.search(r"([0-9A-Za-z_]{15,40})", narration)
                ref = ref_m.group(1) if ref_m else None

                meta: dict[str, Any] = {
                    "amount": d or c,
                    "debit": d,
                    "credit": c,
                    "transaction_type": ttype,
                    "date": date_val,
                    "category": cat,
                    "transaction_number": ref,
                }
                records.append(self._build_record(line_str, meta, source))
                continue

            # 2. Table row pattern: |2025-10-07T09:...|...|0.00 49,900.00|...|
            if line_str.startswith("|"):
                cols = [c.strip() for c in line_str.split("|")[1:-1]]
                if len(cols) >= 3:
                    date_val = self._parse_iso_date(cols[0])
                    if not date_val:
                        continue

                    narration = cols[1] if len(cols) > 1 else cols[0]
                    num_col = cols[2] if len(cols) > 2 else ""

                    amounts = re.findall(r"([0-9,]+\.\d{2})", num_col)
                    deb_val: str | None = None
                    cred_val: str | None = None
                    if len(amounts) >= 2:
                        deb_val = self._clean_number(amounts[0]) if amounts[0] != "0.00" else None
                        cred_val = self._clean_number(amounts[1]) if amounts[1] != "0.00" else None
                    elif len(amounts) == 1:
                        val = self._clean_number(amounts[0])
                        tt, dv, cv = classify_transaction_type(narration, val)
                        deb_val, cred_val = dv, cv

                    ttype, d, c = self._resolve_dc(deb_val, cred_val, narration)
                    cat = categorise_transaction(narration)

                    ref_m = re.search(r"([0-9A-Za-z_]{15,40})", narration)
                    ref = ref_m.group(1) if ref_m else None

                    meta = {
                        "amount": d or c,
                        "debit": d,
                        "credit": c,
                        "transaction_type": ttype,
                        "date": date_val,
                        "category": cat,
                        "transaction_number": ref,
                    }
                    records.append(self._build_record(line_str, meta, source))

        if only_bills:
            records = [r for r in records if self._is_bill_transaction(r)]

        return normalized, records

    def _parse_iso_date(self, val: str) -> str | None:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", val)
        return m.group(1) if m else None

    def _resolve_dc(self, deb_val: str | None, cred_val: str | None, narration: str) -> tuple[str | None, str | None, str | None]:
        if deb_val and not cred_val:
            return "debit", deb_val, None
        if cred_val and not deb_val:
            return "credit", None, cred_val
        if deb_val and cred_val:
            ttype, _, _ = classify_transaction_type(narration)
            if ttype == "credit":
                return "credit", None, cred_val
            return "debit", deb_val, None
        ttype, d, c = classify_transaction_type(narration)
        return ttype, d, c
