"""VBank (VFD Microfinance Bank) statement extractor — space-separated layout."""

from __future__ import annotations

import re
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.receipt_metadata import categorise_transaction, classify_transaction_type


class VBankExtractor(BaseBankExtractor):
    """Extractor for VBank statements with space-separated columns and multi-line narration."""

    bank_name: str = "VBank"
    bank_code: str = "vbank"
    aliases: tuple[str, ...] = ("v_bank",)

    def detect(self, text: str) -> bool:
        head = text[:3000].lower()
        return (
            "vbank" in head
            or ("vfd microfinance bank" in head and "bankwebsite:www.vbank.ng" in head)
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

        # Account name sits on the very first line, before STATEMENT
        vb_name = re.search(
            r"(?:##\s*)?([A-Za-z0-9\s,\.\-&]+?)\s*[\r\n]+\s*(?:###\s*)?(?:<u>\s*)?STATEMENT",
            head,
            re.IGNORECASE,
        )
        if vb_name:
            account_name = vb_name.group(1).strip().strip("#").strip()

        # NUBAN/account number — pipe-delimited: |NUBAN Product Name|1034598335||
        vb_acc = re.search(r"NUBAN\s+Product\s+Name\s*\|+\s*(\d{10})", head, re.IGNORECASE)
        if vb_acc:
            account_number = vb_acc.group(1).strip()

        # Product type (e.g. "Corporate Current Account" as inline text)
        if "corporate current account" in head_lower:
            account_type = "Corporate Current Account"

        # Pipe-delimited numeric fields: |Opening Balance|988,490.49||
        vb_open = re.search(r"Opening\s+Balance\s*\|+\s*([0-9,]+\.\d{2})", head, re.IGNORECASE)
        if vb_open:
            opening_balance = self._clean_number(vb_open.group(1))

        vb_close = re.search(r"Closing\s+Balance\s*\|+\s*([0-9,]+\.\d{2})", head, re.IGNORECASE)
        if vb_close:
            closing_balance = self._clean_number(vb_close.group(1))

        vb_deb = re.search(r"Total\s+Debit\s+Amount\s*\|+\s*([0-9,]+\.\d{2})", head, re.IGNORECASE)
        if vb_deb:
            total_debit = self._clean_number(vb_deb.group(1))

        vb_cred = re.search(r"Total\s+Credit\s+Amount\s*\|+\s*([0-9,]+\.\d{2})", head, re.IGNORECASE)
        if vb_cred:
            total_credit = self._clean_number(vb_cred.group(1))

        # Currency: |Currency||NGN|
        curr_m = re.search(r"Currency\s*\|+\s*([A-Z]{3})", head, re.IGNORECASE)
        if curr_m:
            currency = curr_m.group(1).strip()

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

        source = source_name or "vbank_statement"
        records: list[TransactionRecord] = []

        pending_date: str | None = None
        pending_debit: str | None = None
        pending_credit: str | None = None
        pending_amount: str | None = None
        pending_ttype: str | None = None
        pending_narration_parts: list[str] = []

        def flush() -> None:
            if pending_date and (pending_amount or pending_narration_parts):
                narration = " ".join(pending_narration_parts).strip() or "transaction"
                cat = categorise_transaction(narration)
                ttype = pending_ttype
                d = pending_debit
                c = pending_credit
                amt = pending_amount
                if not ttype:
                    ttype, d, c = classify_transaction_type(narration, amt)
                    amt = d or c or amt
                meta: dict[str, Any] = {
                    "amount": amt,
                    "debit": d,
                    "credit": c,
                    "transaction_type": ttype,
                    "date": pending_date,
                    "category": cat,
                    "transaction_number": None,
                }
                records.append(self._build_record(narration, meta, source))

        header_markers = {
            "transactiondate debit credit balance narration",
            "statementperiod",
            "openingbalance",
            "closingbalance",
            "totalamountonhold",
            "totaldebitamount",
            "totalcreditamount",
            "currency ngn",
            "nuban ",
            "productname ",
            "signature/date",
            "vfdmicrofinancebanklimited",
            "contactemail:",
            "contactphonenumber:",
            "bankwebsite:",
        }

        for line in normalized.split("\n"):
            line_str = line.strip()
            if not line_str:
                flush()
                pending_date = None
                pending_debit = None
                pending_credit = None
                pending_amount = None
                pending_ttype = None
                pending_narration_parts = []
                continue

            lowered = line_str.lower()
            if any(h in lowered for h in header_markers):
                flush()
                pending_date = None
                pending_debit = None
                pending_credit = None
                pending_amount = None
                pending_ttype = None
                pending_narration_parts = []
                continue

            m = re.match(
                r"^(\d{4}-\d{2}-\d{2})\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s*(.*)$",
                line_str,
            )
            if m:
                flush()
                pending_date = m.group(1)
                deb_str = m.group(2)
                cred_str = m.group(3)
                narration_tail = m.group(5).strip()

                deb_val = self._clean_number(deb_str) if deb_str != "0.00" else None
                cred_val = self._clean_number(cred_str) if cred_str != "0.00" else None

                pending_debit = deb_val
                pending_credit = cred_val
                if deb_val:
                    pending_ttype = "debit"
                    pending_amount = deb_val
                elif cred_val:
                    pending_ttype = "credit"
                    pending_amount = cred_val
                else:
                    pending_ttype = None
                    pending_amount = None

                pending_narration_parts = [narration_tail] if narration_tail else []
                continue

            if pending_date:
                pending_narration_parts.append(line_str)
            else:
                if line_str.startswith("|"):
                    cols = [c.strip() for c in line_str.split("|")[1:-1]]
                    if len(cols) >= 4:
                        date_val = self._parse_iso_date(cols[0])
                        if date_val:
                            flush()
                            deb_str = cols[1] if len(cols) > 1 else ""
                            cred_str = cols[2] if len(cols) > 2 else ""
                            deb_val = self._clean_number(deb_str) if deb_str != "0.00" else None
                            cred_val = self._clean_number(cred_str) if cred_str != "0.00" else None
                            narration = " ".join(c for c in cols[4:] if c) if len(cols) > 4 else cols[3]
                            if deb_val:
                                ttype, amt = "debit", deb_val
                            elif cred_val:
                                ttype, amt = "credit", cred_val
                            else:
                                ttype, d, c = classify_transaction_type(narration)
                                amt = d or c
                                deb_val, cred_val = d, c
                            cat = categorise_transaction(narration)
                            meta = {
                                "amount": amt,
                                "debit": deb_val,
                                "credit": cred_val,
                                "transaction_type": ttype,
                                "date": date_val,
                                "category": cat,
                                "transaction_number": None,
                            }
                            records.append(self._build_record(line_str, meta, source))

        flush()

        if only_bills:
            records = [r for r in records if self._is_bill_transaction(r)]

        return normalized, records

    def _parse_iso_date(self, val: str) -> str | None:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", val)
        return m.group(1) if m else None
