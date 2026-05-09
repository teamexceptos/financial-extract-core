from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from models.verification import GoogleLatLngVerifyResult


def google_verify_address_from_lat_lng_service(lat: float, lng: float) -> GoogleLatLngVerifyResult:
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return GoogleLatLngVerifyResult(status="not_configured")

    query = urllib.parse.urlencode({"latlng": f"{lat},{lng}", "key": api_key})
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{query}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return GoogleLatLngVerifyResult(status="error")

    results = payload.get("results") or []
    if not results:
        return GoogleLatLngVerifyResult(status="no_results", raw=payload)

    top = results[0]
    return GoogleLatLngVerifyResult(
        status="ok",
        formatted_address=top.get("formatted_address"),
        place_id=top.get("place_id"),
        raw=payload,
    )

