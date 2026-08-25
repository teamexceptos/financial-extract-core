"""Nigerian Bank Extractors Registry and Dispatcher (services/banks/ng/)."""

from __future__ import annotations

from models.transaction import TransactionRecord, TransactionSummary
from services.banks.ng.access import AccessBankExtractor
from services.banks.ng.base import BaseBankExtractor
from services.banks.ng.general import GeneralBankExtractor
from services.banks.ng.gtbank import GTBankExtractor
from services.banks.ng.kuda import KudaExtractor
from services.banks.ng.moniepoint import MoniepointExtractor
from services.banks.ng.opay import OPayExtractor
from services.banks.ng.palmpay import PalmPayExtractor
from services.banks.ng.parallex import ParallexExtractor
from services.banks.ng.uba import UBAExtractor
from services.banks.ng.vbank import VBankExtractor
from services.banks.ng.vfd import VFDExtractor
from services.banks.ng.zenith import ZenithBankExtractor

_EXTRACTORS: list[BaseBankExtractor] = [
    OPayExtractor(),
    # Kuda precedes GTBank: Kuda statements name counterparty banks ("…/Gtbank Plc") in
    # their opening rows, and GTBank's detect matches a bare "gtbank". Kuda's own detect
    # requires Kuda issuer branding, so it cannot claim another bank's statement.
    KudaExtractor(),
    GTBankExtractor(),
    PalmPayExtractor(),
    MoniepointExtractor(),
    VBankExtractor(),
    VFDExtractor(),
    UBAExtractor(),
    ParallexExtractor(),
    AccessBankExtractor(),
    ZenithBankExtractor(),
]

_GENERAL_EXTRACTOR = GeneralBankExtractor()

# Build fast lookup dictionary by canonical code and aliases
BANK_EXTRACTORS: dict[str, BaseBankExtractor] = {}
for ext in _EXTRACTORS:
    BANK_EXTRACTORS[ext.bank_code] = ext
    for alias in ext.aliases:
        BANK_EXTRACTORS[alias] = ext

BANK_EXTRACTORS["general"] = _GENERAL_EXTRACTOR
BANK_EXTRACTORS["default"] = _GENERAL_EXTRACTOR
BANK_EXTRACTORS["fallback"] = _GENERAL_EXTRACTOR


def detect_bank_type(text: str) -> str | None:
    """Inspect statement text and return the matching bank code if detected."""
    if not text:
        return None
    header = text[:8000]
    for ext in _EXTRACTORS:
        if ext.detect(header):
            return ext.bank_code
    return None


def get_bank_extractor(
    bank_name: str | None = None,
    text: str | None = None,
) -> BaseBankExtractor:
    """Retrieve the appropriate bank extractor instance.

    1. If `bank_name` is explicitly provided, look it up in the registry.
    2. If `bank_name` is omitted and `text` is given, auto-detect from header text.
    3. Fallback to `GeneralBankExtractor`.
    """
    if bank_name:
        key = bank_name.strip().lower().replace(" ", "_").replace("-", "_")
        if key in BANK_EXTRACTORS:
            return BANK_EXTRACTORS[key]

    if text:
        detected_code = detect_bank_type(text)
        if detected_code and detected_code in BANK_EXTRACTORS:
            return BANK_EXTRACTORS[detected_code]

    return _GENERAL_EXTRACTOR


def extract_statement_summary(
    text: str,
    bank: str | None = None,
) -> TransactionSummary | None:
    """Extract account summary details using the resolved bank extractor."""
    if not text:
        return None
    extractor = get_bank_extractor(bank_name=bank, text=text)
    return extractor.extract_summary(text)


def extract_bank_transactions(
    text: str,
    *,
    bank: str | None = None,
    source_name: str | None = None,
    only_bills: bool = False,
) -> tuple[str, list[TransactionRecord]]:
    """Extract transactions using the resolved bank extractor."""
    extractor = get_bank_extractor(bank_name=bank, text=text)
    return extractor.extract(text, source_name=source_name, only_bills=only_bills)


__all__ = [
    "AccessBankExtractor",
    "BANK_EXTRACTORS",
    "BaseBankExtractor",
    "GTBankExtractor",
    "GeneralBankExtractor",
    "KudaExtractor",
    "MoniepointExtractor",
    "OPayExtractor",
    "PalmPayExtractor",
    "ParallexExtractor",
    "UBAExtractor",
    "VBankExtractor",
    "VFDExtractor",
    "ZenithBankExtractor",
    "detect_bank_type",
    "extract_bank_transactions",
    "extract_statement_summary",
    "get_bank_extractor",
]
