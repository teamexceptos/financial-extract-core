from __future__ import annotations

import re
from typing import Any

from utils.date_recency import compute_date_recency


def extract_receipt_metadata_from_text(text: str) -> dict[str, Any]:
    receipt_number: str | None = None
    date_iso: str | None = None
    receipt_date = None
    amount: str | None = None
    address: str | None = None
    receipt_type: str | None = None

    m = re.search(r"(?i)\breceipt\s*(?:no|number)\s*[:#]?\s*([A-Z0-9/\-_.]+)", text)
    if m:
        receipt_number = m.group(1).strip()
    else:
        m2 = re.search(r"\bRCPT/[A-Z0-9/\-_.]+\b", text)
        if m2:
            receipt_number = m2.group(0).strip()
        else:
            m3 = re.search(r"(?i)\btransaction\s*id\s*[:#]?\s*([A-Z0-9/\-_.]+)", text)
            if m3:
                receipt_number = m3.group(1).strip()
            else:
                m4 = re.search(r"(?i)\breference\s*[:#]?\s*([A-Z0-9/\-_.]+)", text)
                if m4:
                    receipt_number = m4.group(1).strip()

    date_match = re.search(r"(?i)\bdate\s*[:\-]\s*([^\n\r]+)", text)
    if date_match:
        raw_date = date_match.group(1).strip()
        try:
            from dateutil import parser as date_parser

            dt = date_parser.parse(raw_date, fuzzy=True, dayfirst=True)
            receipt_date = dt.date()
            date_iso = receipt_date.isoformat()
        except Exception:
            date_iso = raw_date
    else:
        dt_match = re.search(r"(?i)\b(?:transaction\s*time|completion\s*time)\s*(?::|-)?\s*([^\n\r]+)", text)
        if dt_match:
            raw_dt = dt_match.group(1).strip()
            try:
                from dateutil import parser as date_parser

                dt = date_parser.parse(raw_dt, fuzzy=True, dayfirst=True)
                receipt_date = dt.date()
                date_iso = receipt_date.isoformat()
            except Exception:
                date_iso = raw_dt

    amount_match = re.search(r"(?i)\bpayment\s*amount\b[^\d]*([0-9][0-9,]*(?:\.\d{2})?)", text)
    if not amount_match:
        amount_match = re.search(r"(?i)\border\s*amount\b[^\d]*([0-9][0-9,]*(?:\.\d{2})?)", text)
    if amount_match:
        try:
            amount = f"{float(amount_match.group(1).replace(',', '')):.2f}"
        except Exception:
            amount = amount_match.group(1)
    else:
        number_candidates = re.findall(r"(?<!\d)(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)(?!\d)", text)
        best_value: float | None = None
        for token in number_candidates:
            try:
                val = float(token.replace(",", ""))
            except Exception:
                continue
            if best_value is None or val > best_value:
                best_value = val
        if best_value is not None:
            amount = f"{best_value:.2f}"

    lines_raw = [ln.strip() for ln in re.split(r"[\r\n]+", text)]
    lines = [re.sub(r"\s+", " ", ln).strip(" –—") for ln in lines_raw if ln and ln.strip()]
    lines = [ln for ln in lines if not re.match(r"(?i)^printed\s+today\b", ln)]

    idx_address = next((i for i, ln in enumerate(lines) if ln.strip().upper() == "ADDRESS"), None)
    if idx_address is not None:
        chunk: list[str] = []
        for ln in lines[idx_address + 1 :]:
            if re.match(r"(?i)^(applicable\s+country|status|reference|transaction\s+id|transaction\s+time|completion\s+time)\b", ln):
                break
            chunk.append(ln)
        chunk = [ln.strip(" –—") for ln in chunk if ln.strip()]
        parts: list[str] = []
        for ln in chunk:
            if parts and parts[-1].endswith("-"):
                parts[-1] = parts[-1] + ln.lstrip()
            else:
                parts.append(ln)
        address_joined = " ".join(parts)
        address = address_joined.strip() or None
    else:
        idx_official = next((i for i, ln in enumerate(lines) if "OFFICIAL RECEIPT" in ln.upper()), None)
        if idx_official is not None and idx_official > 0:
            candidate = lines[:idx_official]
            candidate = candidate[:4]
            address = ", ".join(candidate).strip() or None
        elif lines:
            address = ", ".join(lines[:3]).strip() or None

    days_from_today, is_within_3_months = compute_date_recency(receipt_date)

    text_l = text.lower()
    if re.search(r"\belectric(ity)?\b", text_l) or "meter" in text_l or "token" in text_l or "units" in text_l:
        if re.search(r"\b(prepaid|token)\b", text_l):
            receipt_type = "electricity_prepaid"
        elif re.search(r"\bpostpaid\b", text_l):
            receipt_type = "electricity_postpaid"
        else:
            receipt_type = "electricity"
    elif re.search(r"\bwater\b", text_l):
        receipt_type = "water"
    elif re.search(r"\bwaste\b|\brefuse\b|\btrash\b|\bsanitation\b", text_l):
        receipt_type = "waste_management"
    elif re.search(r"\bcable\b|\bdstv\b|\bgotv\b|\bstartimes\b", text_l):
        receipt_type = "cable_tv"
    elif re.search(r"\binternet\b|\bdata\b|\bbroadband\b|\bmb\b|\bgb\b", text_l):
        receipt_type = "internet_data"
    elif re.search(r"\bgas\b|\bcooking\s*gas\b|\blpg\b|\bcylinder\b|\brefill\b", text_l):
        receipt_type = "cooking_gas_refill"

    return {
        "address": address,
        "amount": amount,
        "date": date_iso,
        "days_from_today": days_from_today,
        "is_within_3_months": is_within_3_months,
        "receipt_number": receipt_number,
        "receipt_type": receipt_type,
    }
