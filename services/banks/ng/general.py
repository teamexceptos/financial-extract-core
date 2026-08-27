"""General fallback extractor for unrecognized bank statements."""

from __future__ import annotations

import re
from typing import Any

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.base import BaseBankExtractor
from utils.receipt_metadata import categorise_transaction, classify_transaction_type


class GeneralBankExtractor(BaseBankExtractor):
    """General table and row parser fallback for any statement format."""

    bank_name: str = "General Bank"
    bank_code: str = "general"
    aliases: tuple[str, ...] = ("default", "fallback", "unknown")

    _HEADER_KEYWORDS = {
        "opening balance", "closing balance", "usable balance", "available balance",
        "total debit", "total credit", "total debits", "total credits",
        "account summary", "statement period", "print. date", "branch name",
        "account no", "account number", "account name", "customer name",
        "customer statement", "account statement", "transaction summary",
        "balance brought forward", "balance carried forward",
        "date narration reference debit credit balance",
        "trans. date|value. date", "transaction date", "value date",
        "statement of account", "account type", "currency",
        "total amount on hold", "total debit amount", "total credit amount",
        "this is a computer generated", "signature/date",
        "debit count", "credit count",
    }

    def detect(self, text: str) -> bool:
        return True

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

        source = source_name or "general_statement"
        lines = normalized.split("\n")
        records: list[TransactionRecord] = []
        seen_keys: set[tuple[str | None, str | None, str]] = set()

        pending_date: str | None = None
        pending_debit: str | None = None
        pending_credit: str | None = None
        pending_amount: str | None = None
        pending_ttype: str | None = None
        pending_narration_parts: list[str] = []

        def dedup_key(date_val: str | None, amt: str | None, desc: str) -> tuple[str | None, str | None, str]:
            short = desc[:80].lower()
            return (date_val, amt, short)

        def flush_pending() -> None:
            if not pending_date:
                return
            if not (pending_amount or pending_narration_parts):
                return
            narration = " ".join(pending_narration_parts).strip() or "transaction"
            ttype = pending_ttype
            d = pending_debit
            c = pending_credit
            amt = pending_amount
            if not ttype:
                ttype, d, c = classify_transaction_type(narration, amt)
                amt = d or c or amt
            cat = categorise_transaction(narration)
            key = dedup_key(pending_date, amt, narration)
            if key in seen_keys:
                return
            seen_keys.add(key)
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

        for idx, line in enumerate(lines):
            line_str = line.strip()
            if not line_str or line_str.startswith("|---"):
                flush_pending()
                pending_date = None
                pending_debit = None
                pending_credit = None
                pending_amount = None
                pending_ttype = None
                pending_narration_parts = []
                continue

            lowered = line_str.lower()
            if any(h in lowered for h in self._HEADER_KEYWORDS):
                flush_pending()
                pending_date = None
                pending_debit = None
                pending_credit = None
                pending_amount = None
                pending_ttype = None
                pending_narration_parts = []
                continue

            added = False

            if line_str.startswith("|"):
                cols = [c.strip() for c in line_str.split("|")[1:-1]]
                if len(cols) >= 3:
                    date_val = self._parse_date(" ".join(cols[:3]))
                    if date_val or self._has_amount_pattern(cols):
                        result = self._parse_columns(cols, date_val)
                        if result:
                            d_val, c_val, amt, ttype, desc, dt, ref = result
                            if not (dt or amt):
                                added = False
                                continue
                            flush_pending()
                            cat = categorise_transaction(desc)
                            key = dedup_key(dt, amt, desc)
                            if key not in seen_keys:
                                seen_keys.add(key)
                                meta = {
                                    "amount": amt,
                                    "debit": d_val,
                                    "credit": c_val,
                                    "transaction_type": ttype,
                                    "date": dt,
                                    "category": cat,
                                    "transaction_number": ref,
                                }
                                records.append(self._build_record(line_str, meta, source))
                            added = True

            if added:
                continue

            struct = self._parse_structured_row(line_str)
            if struct:
                flush_pending()
                dt, d_val, c_val, amt, ttype, desc, ref = struct
                cat = categorise_transaction(desc)
                key = dedup_key(dt, amt, desc)
                if key not in seen_keys:
                    seen_keys.add(key)
                    meta = {
                        "amount": amt,
                        "debit": d_val,
                        "credit": c_val,
                        "transaction_type": ttype,
                        "date": dt,
                        "category": cat,
                        "transaction_number": ref,
                    }
                    records.append(self._build_record(line_str, meta, source))
                continue

            date_val = self._parse_date(line_str)
            if date_val and (self._line_has_amount(line_str) or pending_date is None):
                flush_pending()
                amounts = self._extract_all_amounts(line_str)
                desc = self._strip_date_and_amounts(line_str, date_val, amounts)
                d_val, c_val, amt, ttype = self._resolve_amounts(amounts, desc)
                pending_date = date_val
                pending_debit = d_val
                pending_credit = c_val
                pending_amount = amt
                pending_ttype = ttype
                pending_narration_parts = [desc] if desc else []
                continue

            if date_val and self._line_has_amount(line_str):
                amounts = self._extract_all_amounts(line_str)
                desc = self._strip_date_and_amounts(line_str, date_val, amounts)
                d_val, c_val, amt, ttype = self._resolve_amounts(amounts, desc or line_str)
                full_desc = desc or line_str
                cat = categorise_transaction(full_desc)
                key = dedup_key(date_val, amt, full_desc)
                if key not in seen_keys:
                    seen_keys.add(key)
                    meta = {
                        "amount": amt,
                        "debit": d_val,
                        "credit": c_val,
                        "transaction_type": ttype,
                        "date": date_val,
                        "category": cat,
                        "transaction_number": None,
                    }
                    records.append(self._build_record(line_str, meta, source))
                continue

            if pending_date and not self._looks_like_header(line_str):
                pending_narration_parts.append(line_str)
            # else: ignore stray line

        flush_pending()

        if only_bills:
            records = [r for r in records if self._is_bill_transaction(r)]

        return normalized, records

    def _has_amount_pattern(self, cols: list[str]) -> bool:
        for c in cols:
            if re.search(r"[0-9]{1,3}(?:,[0-9]{3})*\.\d{2}", c):
                return True
            if re.fullmatch(r"\d{1,3}(?:,[0-9]{3})*\.\d{2}", c.strip()):
                return True
        return False

    def _line_has_amount(self, text: str) -> bool:
        return bool(re.search(r"(?<![A-Za-z0-9])(?:₦|\$)?\s*\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})(?![A-Za-z0-9])", text))

    def _extract_all_amounts(self, text: str) -> list[str]:
        pattern = (
            r"(?:₦|\$)\s*([+-]?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})|\d+\.\d{1,2})|"
            r"(?<![A-Za-z0-9_-])([+-]?\d{1,3}(?:,[0-9]{3})+\.\d{2})|"
            r"(?<![A-Za-z0-9_-])([+-]?\d{1,3}(?:\s[0-9]{3})+\.\d{2})|"
            r"(?<=\s)([+-]?\d+\.\d{2})(?=\s|$|[A-Za-z])"
        )
        raws = re.findall(pattern, text)
        cleaned: list[str] = []
        for group in raws:
            for r in group:
                if not r:
                    continue
                c = self._clean_number(r)
                if c is not None:
                    cleaned.append(c)
        return cleaned

    def _strip_date_and_amounts(self, text: str, date_val: str | None, amounts: list[str]) -> str:
        s = text
        if date_val:
            # remove original date occurrence first
            s = re.sub(
                r"\d{4}-\d{2}-\d{2}T[\d:]+|"
                r"\d{4}-\d{2}-\d{2}|"
                r"\d{1,2}[-/][A-Za-z0-9]{3}[-/]\d{2,4}|"
                r"\d{1,2}[-/]\d{2}[-/]\d{2,4}|"
                r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}",
                " ", s, count=1,
            )
        # remove each occurrence of clean-looking numbers/money
        s = re.sub(r"(?:₦|\$)?\s*[+-]?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?", " ", s)
        s = re.sub(r"[|]+", " ", s)
        s = re.sub(r"\s{2,}", " ", s).strip(" :|,")
        return s

    def _resolve_amounts(self, amounts: list[str], narration: str) -> tuple[str | None, str | None, str | None, str | None]:
        if not amounts:
            t, d, c = classify_transaction_type(narration or "")
            return d, c, (d or c), t
        nums = [float(a) for a in amounts if a is not None]
        if len(nums) >= 3:
            deb_raw = amounts[0]
            cred_raw = amounts[1]
            if nums[0] > 0 and nums[0] != nums[-1]:
                d, c = deb_raw, None
                return d, c, d, "debit"
            if nums[1] > 0 and nums[1] != nums[-1]:
                d, c = None, cred_raw
                return d, c, c, "credit"
            d, c, amt = None, None, amounts[-1]
            t, dd, cc = classify_transaction_type(narration or "", amt)
            return dd, cc, (dd or cc or amt), t
        if len(nums) == 2:
            a, b = amounts[0], amounts[1]
            na, nb = nums[0], nums[1]
            narr_clean = (narration or "").strip()
            if na == nb:
                amt = a
                t, d, c = classify_transaction_type(narr_clean, amt)
                return d, c, (d or c or amt), t
            t, d_cls, c_cls = classify_transaction_type(narr_clean or "transaction")
            if t == "debit":
                return a, None, a, "debit"
            if t == "credit":
                return None, b, b, "credit"
            if na > 0 and na != nb and nb > 0:
                if narr_clean:
                    t2, d2, c2 = classify_transaction_type(narr_clean, a)
                    if t2 == "credit":
                        return None, b, b, "credit"
                return a, None, a, "debit"
            candidate = b if na == 0 else a
            t3, d3, c3 = classify_transaction_type(narr_clean or "transaction", candidate)
            return d3, c3, (d3 or c3 or candidate), t3
        amt = amounts[0]
        t, d, c = classify_transaction_type((narration or "").strip() or "transaction", amt)
        return d, c, (d or c or amt), t

    def _looks_like_header(self, text: str) -> bool:
        l = text.lower()
        if len(l) > 120:
            return False
        return any(k in l for k in ["date", "narration", "description", "debit", "credit", "balance", "reference", "value"]) and len(l.split()) >= 3

    def _parse_columns(self, cols: list[str], date_val: str | None):
        joined = " ".join(cols)
        if not date_val:
            date_val = self._parse_date(joined)
        non_empty = [c for c in cols if c]
        amounts_cols: list[tuple[int, str]] = []
        for i, c in enumerate(cols):
            if re.fullmatch(r"(?:₦|\$)?\s*[+-]?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})|--?", c.strip()):
                clean = self._clean_number(c.strip())
                if clean is not None:
                    amounts_cols.append((i, clean))
        d_val: str | None = None
        c_val: str | None = None
        amt: str | None = None
        ttype: str | None = None
        if len(amounts_cols) >= 3:
            first_val = amounts_cols[0][1]
            second_val = amounts_cols[1][1]
            last_val = amounts_cols[-1][1]
            d_raw = first_val if float(first_val) > 0 else None
            c_raw = second_val if float(second_val) > 0 else None
            if d_raw and not c_raw:
                d_val, c_val, amt, ttype = d_raw, None, d_raw, "debit"
            elif c_raw and not d_raw:
                d_val, c_val, amt, ttype = None, c_raw, c_raw, "credit"
            elif not d_raw and not c_raw:
                amt = last_val
                ttype, dd, cc = classify_transaction_type(" ".join(non_empty), amt)
                d_val, c_val = dd, cc
                amt = dd or cc or amt
            else:
                narr = " ".join(non_empty)
                ttype, _, _ = classify_transaction_type(narr, d_raw or c_raw)
                if ttype == "credit":
                    d_val, c_val, amt = None, c_raw, c_raw
                else:
                    d_val, c_val, amt = d_raw, None, d_raw
        elif len(amounts_cols) == 2:
            first_idx, first_val = amounts_cols[0]
            last_idx, last_val = amounts_cols[-1]
            narr = " ".join(non_empty)
            ttype_guess, _, _ = classify_transaction_type(narr, first_val or last_val)
            if float(first_val) == 0:
                c_val, amt, ttype = last_val, last_val, "credit"
            elif float(last_val) == 0:
                d_val, amt, ttype = first_val, first_val, "debit"
            elif first_idx < last_idx:
                candidate = first_val
                tt, dd, cc = classify_transaction_type(narr, candidate)
                d_val, c_val = dd, cc
                amt = dd or cc or candidate
                ttype = tt
            else:
                candidate = last_val
                tt, dd, cc = classify_transaction_type(narr, candidate)
                d_val, c_val = dd, cc
                amt = dd or cc or candidate
                ttype = tt
        elif len(amounts_cols) == 1:
            amt = amounts_cols[0][1]
            ttype, d_val, c_val = classify_transaction_type(" ".join(non_empty), amt)
            amt = d_val or c_val or amt
        else:
            all_amounts = self._extract_all_amounts(joined)
            if not all_amounts and not date_val:
                return None
            amt = all_amounts[-1] if all_amounts else None
            ttype, d_val, c_val = classify_transaction_type(" ".join(non_empty), amt)
            amt = d_val or c_val or amt

        desc_parts: list[str] = []
        amount_idxs = {i for i, _ in amounts_cols}
        for i, c in enumerate(cols):
            if i in amount_idxs:
                continue
            if i == 0 and date_val and self._parse_date(c):
                continue
            if c.strip():
                desc_parts.append(c.strip())
        desc = " ".join(desc_parts).strip() or " ".join(non_empty)

        ref = None
        for c in cols:
            m = re.search(r"\b([A-Z0-9]{8,40}|TXN\d{6,}|REF\d{6,})\b", c)
            if m:
                ref = m.group(1)
                break

        return d_val, c_val, amt, ttype, desc, date_val, ref

    def _parse_structured_row(self, line_str: str):
        date_prefixes = [
            r"^(\d{4}-\d{2}-\d{2})(?:T[\d:]+)?\s+",
            r"^(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+",
            r"^(\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+",
            r"^(\d{1,2}/[A-Za-z]{3}/\d{2,4})\s+",
            r"^(\d{2}/\d{2}/\d{2,4})\s+",
            r"^(\d{2}-\d{2}-\d{2,4})\s+",
            r"^(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+",
        ]
        dt_raw: str | None = None
        rest: str | None = None
        for p in date_prefixes:
            m = re.match(p, line_str)
            if m:
                dt_raw = m.group(1)
                rest = line_str[m.end():]
                break
        if not dt_raw or not rest:
            return None
        dt = self._parse_date(dt_raw)
        if not dt:
            return None

        trail_m = re.finditer(
            r"(?<![A-Za-z0-9])(?:₦|\$)?\s*([+-]?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2}))(?=\s+|$)",
            rest,
        )
        trailing: list[tuple[int, int, str]] = []
        for mm in trail_m:
            trailing.append((mm.start(), mm.end(), mm.group(1)))

        right_edge: list[tuple[int, int, str]] = []
        nxt_end = len(rest.rstrip())
        for s, e, g in reversed(trailing):
            if e == nxt_end or (e < nxt_end and rest[e:nxt_end].strip() == ""):
                right_edge.append((s, e, g))
                prev_idx = s
                while prev_idx > 0 and rest[prev_idx - 1] in " \t":
                    prev_idx -= 1
                nxt_end = prev_idx
            else:
                break
        right_edge.reverse()

        amounts = [self._clean_number(g) for _, _, g in right_edge]
        amounts = [a for a in amounts if a is not None]
        if len(amounts) == 0:
            amounts = self._extract_all_amounts(rest)
            if not amounts:
                return None
            narr_start = 0
        else:
            narr_start = 0
            if right_edge:
                narr_start = right_edge[0][0]
        desc = rest[:narr_start].strip() if narr_start > 0 else self._strip_date_and_amounts(rest, None, amounts)
        desc = desc.strip() or rest.strip()

        d_val, c_val, amt, ttype = self._resolve_amounts(amounts, desc)
        return dt, d_val, c_val, amt, ttype, desc, None
