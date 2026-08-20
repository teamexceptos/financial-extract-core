"""Access Bank statement extractor."""

from __future__ import annotations

import re
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.receipt_metadata import categorise_transaction, classify_transaction_type


class AccessBankExtractor(BaseBankExtractor):
    """Extractor for Access Bank statements."""

    bank_name: str = "Access Bank"
    bank_code: str = "access"
    aliases: tuple[str, ...] = ("access_bank", "access_ng", "access_bank_plc")

    def detect(self, text: str) -> bool:
        head = text[:8000].lower()
        return (
            "access bank" in head
            or "accessbankplc.com" in head
            or ("access" in head and "diamond" in head)
        )

    def extract_summary(self, text: str) -> TransactionSummary | None:
        return super().extract_summary(text)

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

        source = source_name or "access_statement"
        records: list[TransactionRecord] = []

        for line in normalized.split("\n"):
            line_str = line.strip()
            if not line_str or line_str.startswith("|---"):
                continue

            lowered = line_str.lower()
            if any(t in lowered for t in ["statement period", "account statement", "opening balance", "closing balance"]):
                continue

            if line_str.startswith("|"):
                cols = [c.strip() for c in line_str.split("|")[1:-1]]
                if len(cols) >= 4:
                    date_val = self._parse_date(cols[0])
                    if not date_val:
                        continue

                    narration = cols[1] if len(cols) > 1 else cols[0]
                    deb_str = cols[2] if len(cols) > 2 else ""
                    cred_str = cols[3] if len(cols) > 3 else ""

                    deb_val = self._clean_number(deb_str)
                    cred_val = self._clean_number(cred_str)

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

    def _parse_date(self, val: str) -> str | None:
        m = re.search(r"(\d{1,2}[-/][A-Za-z0-9]{3}[-/]\d{2,4})", val)
        if m:
            try:
                from dateutil import parser as dp
                return dp.parse(m.group(1), fuzzy=True, dayfirst=True).date().isoformat()
            except Exception:
                return m.group(1)
        return None
