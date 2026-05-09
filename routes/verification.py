from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from models.verification import (
    AddressInput,
    AddressVerificationResult,
    GoogleLatLngVerifyResult,
    ReceiptCrossRefRequest,
    ReceiptCrossRefResult,
)
from services.address import verify_address_input_service
from services.google import google_verify_address_from_lat_lng_service
from services.receipt import ReceiptExtractionError, cross_reference_receipt_address_service, extract_receipt_text_from_file_bytes

router = APIRouter(prefix="/verification", tags=["Verification"])


@router.post("/address-input", response_model=AddressVerificationResult)
def verify_address_input(address: AddressInput):
    return verify_address_input_service(address)


@router.post("/receipt-crossref", response_model=ReceiptCrossRefResult)
def cross_reference_receipt(req: ReceiptCrossRefRequest):
    address_result = verify_address_input_service(req.address)
    if not address_result.is_structurally_valid:
        raise HTTPException(status_code=422, detail={"issues": address_result.issues})

    return cross_reference_receipt_address_service(req.address, req.receipt_text)


@router.post("/receipt-file-crossref", response_model=ReceiptCrossRefResult)
async def cross_reference_receipt_file(address: str = Form(...), receipt_file: UploadFile = File(...)):
    try:
        parsed_address = AddressInput.model_validate_json(address)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid address JSON")

    address_result = verify_address_input_service(parsed_address)
    if not address_result.is_structurally_valid:
        raise HTTPException(status_code=422, detail={"issues": address_result.issues})

    file_bytes = await receipt_file.read()
    try:
        extracted_text, _source = extract_receipt_text_from_file_bytes(
            file_bytes,
            filename=receipt_file.filename,
            content_type=receipt_file.content_type,
        )
    except ReceiptExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return cross_reference_receipt_address_service(parsed_address, extracted_text)


@router.post("/receipt-extract")
async def extract_receipt_text(receipt_file: UploadFile = File(...)):
    file_bytes = await receipt_file.read()
    try:
        extracted_text, source = extract_receipt_text_from_file_bytes(
            file_bytes,
            filename=receipt_file.filename,
            content_type=receipt_file.content_type,
        )
    except ReceiptExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"source": source, "text": extracted_text}


@router.get("/google-reverse", response_model=GoogleLatLngVerifyResult)
def google_reverse_geocode(lat: float, lng: float):
    return google_verify_address_from_lat_lng_service(lat, lng)
