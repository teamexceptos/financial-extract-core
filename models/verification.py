from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AddressInput(BaseModel):
    line1: str = Field(min_length=1, max_length=128)
    line2: str | None = Field(default=None, max_length=128)
    city: str = Field(min_length=1, max_length=64)
    state: str = Field(min_length=1, max_length=32)
    postal_code: str = Field(min_length=1, max_length=16)
    country: str = Field(default="US", min_length=2, max_length=2)

    @field_validator("country")
    @classmethod
    def _country_upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("state")
    @classmethod
    def _state_upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("postal_code")
    @classmethod
    def _postal_trim(cls, v: str) -> str:
        return v.strip()


class AddressVerificationResult(BaseModel):
    normalized_address: str
    is_structurally_valid: bool
    issues: list[str] = Field(default_factory=list)


class ReceiptCrossRefRequest(BaseModel):
    address: AddressInput
    receipt_text: str = Field(min_length=1)


class ReceiptCrossRefResult(BaseModel):
    normalized_address: str
    similarity_score: float
    match: bool
    signals: list[str] = Field(default_factory=list)


class GoogleLatLngVerifyResult(BaseModel):
    status: Literal["ok", "not_configured", "no_results", "error"]
    formatted_address: str | None = None
    place_id: str | None = None
    raw: dict[str, Any] | None = None