from __future__ import annotations

import re
from typing import Any

from utils.date_recency import compute_date_recency


CREDIT_KEYWORDS = (
    "credit", "cr", "transfer from", "trffrm", "trf from", "tnf-",
    "received from", "deposit", "refund", "reversal", "inward",
    "paid by", "interest capitalised", "cash transfer for"
)

DEBIT_KEYWORDS = (
    "debit", "dr", "transfer to", "trf to", "mobile trf to", "payment", "payment for",
    "payment to", "payment amount", "order amount", "usdt topup", "ussd topup", "mob topup", "topup",
    "charge", "charges", "fee", "purchase", "pos pur", "pos trf",
    "web pur", "vat", "stamp duty", "stamp duties", "sms alert charge",
    "withdraw", "withdrawn", "paid", "paid to", "outward", "commission", "airtime"
)

_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Utility",       ["electric", "nepa", "meter", "token", "phcn", "ekedc", "ibedc", "phed",
                       "water board", "water bill", "lwsc", "ikeja electric", "aedc", "bedc"]),
    ("Cable TV",      ["dstv", "gotv", "startimes", "cable tv", "cable subscription", "multichoice"]),
    ("Internet",      ["internet", "broadband", "fibre", "fiber", "wifi subscription", "data subscription",
                       "spectranet", "smile", "ipnx"]),
    ("Cooking Gas",   ["cooking gas", "lpg", "gas refill", "cylinder refill"]),
    ("Water",         ["water supply", "water token"]),
    ("Airtime",       ["airtime", "recharge", "vtu", "airtel", "9mobile", "etisalat",
                       "mtn airtime", "glo airtime"]),
    ("Data Bundle",   ["data bundle", "data plan", "data top", "data purchase"]),
    ("Salary",        ["salary", "payroll", "wages", "staff salary"]),
    ("Loan",          ["loan disbursement", "loan repayment", "loan deduction", "credit facility"]),
    ("School Fees",   ["school fee", "school stipend", "tuition", "bursary"]),
    ("POS Purchase",  ["pos pur", "pos payment", "pos trf", "payment for goods", "payment for service"]),
    ("ATM",           ["atm withdrawal", "atm cash", "cash withdrawal"]),
    ("Transfer",      ["transfer", "trf", "nip/", "nip ", "nibss", "nipb",
                       "gtw", "api", "br/", "uss", "ussd transfer", "mobile transfer",
                       "transfer between customers", "intra bank", "inter bank",
                       "000013", "000023",          # NIBSS session ID prefixes
                       "to opay", "to palmpay", "to moniepoint", "to providus",
                       "to mfb", "to zenith", "to gtb", "to uba", "to access",
                       "from opay", "from palmpay", "from moniepoint",
                       "guide to", "cash to", "car to", "school stipend to"]),
    ("Bank Charge",   ["vat", "stamp duty", "sms charge", "sms alert", "maintenance fee",
                       "card maintenance", "account maintenance", "commission",
                       "recover partial", "recover charge"]),
]



def categorise_transaction(description: str, receipt_type: str | None = None) -> str | None:
    """
    Return a human-readable category for a transaction.

    Tries receipt_type first (already computed by extract_receipt_metadata_from_text),
    then falls back to keyword matching on *description*.

    Returns None when nothing matches (rather than a noisy "Other" label).
    """
    # Map fine-grained receipt_type → category
    _RECEIPT_TYPE_CATEGORY: dict[str, str] = {
        "electricity_prepaid": "Utility",
        "electricity_postpaid": "Utility",
        "electricity": "Utility",
        "water": "Water",
        "waste_management": "Utility",
        "cable_tv": "Cable TV",
        "internet_data": "Internet",
        "cooking_gas_refill": "Cooking Gas",
    }
    if receipt_type and receipt_type in _RECEIPT_TYPE_CATEGORY:
        return _RECEIPT_TYPE_CATEGORY[receipt_type]

    text = description.lower()
    for category, keywords in _CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return category

    return None


def classify_transaction_type(text: str, amount_val: str | None = None) -> tuple[str | None, str | None, str | None]:
    """
    Classifies a transaction description/text and amount as credit or debit based on symbols & keywords.
    Returns (transaction_type, debit_amount, credit_amount).
    """
    text_clean = text.strip()
    text_lower = text_clean.lower()

    ttype: str | None = None
    # 1. Symbol checks
    if re.search(r"(?:\+|\bCR\b|\(CR\))", text_clean, re.IGNORECASE):
        ttype = "credit"
    elif re.search(r"(?:-|\bDR\b|\(DR\)|\(\d+(?:\.\d+)?\))", text_clean, re.IGNORECASE):
        ttype = "debit"

    # 2. Description keyword checks if symbol check didn't specify
    if not ttype:
        has_credit = any(kw in text_lower for kw in CREDIT_KEYWORDS)
        has_debit = any(kw in text_lower for kw in DEBIT_KEYWORDS)
        if has_credit and not has_debit:
            ttype = "credit"
        elif has_debit and not has_credit:
            ttype = "debit"
        elif has_credit and has_debit:
            # Prefer "transfer from" / inward signals over "transfer to" / outward signals
            if "transfer from" in text_lower or "tnf-" in text_lower or "inward" in text_lower:
                ttype = "credit"
            else:
                ttype = "debit"

    debit_amount: str | None = amount_val if ttype == "debit" else None
    credit_amount: str | None = amount_val if ttype == "credit" else None

    return ttype, debit_amount, credit_amount


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
        dt_match = re.search(r"(?i)\b(?:transaction\s*time|completion\s*time)\s*(?::|-)?s*([^\n\r]+)", text)
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

    transaction_type, debit, credit = classify_transaction_type(text, amount)
    category = categorise_transaction(text, receipt_type)

    return {
        "address": address,
        "amount": amount,
        "credit": credit,
        "date": date_iso,
        "days_from_today": days_from_today,
        "debit": debit,
        "is_within_3_months": is_within_3_months,
        "receipt_number": receipt_number,
        "receipt_type": receipt_type,
        "transaction_type": transaction_type,
        # new fields
        "category": category,
        "transaction_number": receipt_number,   # alias — same value, clearer name for bank statements
    }
