from __future__ import annotations

import re

from models.verification import AddressInput, AddressVerificationResult


def normalize_address(address: AddressInput) -> str:
    parts = [
        address.line1.strip(),
        address.line2.strip() if address.line2 else None,
        f"{address.city.strip()}, {address.state.strip()} {address.postal_code.strip()}",
        address.country.strip().upper(),
    ]
    return ", ".join([p for p in parts if p])


def verify_address_input_service(address: AddressInput) -> AddressVerificationResult:
    normalized = normalize_address(address)
    issues: list[str] = []
    is_valid = True

    if address.country.upper() == "US":
        if not re.fullmatch(r"\d{5}(-\d{4})?", address.postal_code.strip()):
            is_valid = False
            issues.append("Invalid US postal_code format (expected 12345 or 12345-6789)")
        if not re.fullmatch(r"[A-Z]{2}", address.state.strip().upper()):
            is_valid = False
            issues.append("Invalid US state format (expected 2-letter code)")

    for key, value in {
        "line1": address.line1,
        "city": address.city,
        "state": address.state,
        "postal_code": address.postal_code,
        "country": address.country,
    }.items():
        if not value or not value.strip():
            is_valid = False
            issues.append(f"Missing {key}")

    return AddressVerificationResult(
        normalized_address=normalized,
        is_structurally_valid=is_valid,
        issues=issues,
    )

