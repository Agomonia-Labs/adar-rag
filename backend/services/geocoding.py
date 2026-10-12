from __future__ import annotations

"""Thin wrapper around the Google Maps Geocoding API, used to turn a
restaurant's free-text address into a lat/lng + structured city/state so the
marketplace can filter and sort by distance (see routes/restaurant.py's
GET /restaurants, /menu/search, /menu/compare, /menu/recommend).

Deliberately tiny and defensive: geocoding is best-effort enrichment, never a
hard dependency for saving a restaurant. Any failure (missing key, network
error, zero results, API error status) returns None and logs a warning --
callers must keep working with a restaurant that has an address but no
coordinates (it still shows up in name search, just not in radius-based
browsing until it's geocoded, e.g. by the backfill script)."""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def _api_key() -> str:
    return (
        os.getenv("GOOGLE_MAPS_API_KEY")
        or os.getenv("GOOGLE_GEOCODING_API_KEY")
        or os.getenv("GOOGLE_AI_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or ""
    ).strip()


def configured() -> bool:
    return bool(_api_key())


def _extract_component(components: list[dict], type_name: str, *, short: bool = False) -> str:
    for comp in components:
        if type_name in (comp.get("types") or []):
            return comp.get("short_name") if short else comp.get("long_name") or ""
    return ""


async def geocode_address(address: str) -> dict | None:
    """Returns {"latitude": float, "longitude": float, "city": str, "state": str}
    or None if the address couldn't be geocoded (missing/blank address, no API
    key configured, no results, or a request error)."""
    address = (address or "").strip()
    if not address:
        return None
    api_key = _api_key()
    if not api_key:
        logger.warning("Geocoding skipped for %r: no Google Maps API key configured", address)
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(GEOCODE_URL, params={"address": address, "key": api_key})
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("Geocoding request failed for %r", address, exc_info=True)
        return None

    status = data.get("status")
    results = data.get("results") or []
    if status != "OK" or not results:
        logger.warning("Geocoding found no result for %r (status=%s)", address, status)
        return None

    result = results[0]
    location = (result.get("geometry") or {}).get("location") or {}
    lat, lng = location.get("lat"), location.get("lng")
    if lat is None or lng is None:
        return None

    components = result.get("address_components") or []
    city = (
        _extract_component(components, "locality")
        or _extract_component(components, "postal_town")
        or _extract_component(components, "sublocality")
        or _extract_component(components, "administrative_area_level_2")
    )
    state = _extract_component(components, "administrative_area_level_1", short=True)

    return {"latitude": float(lat), "longitude": float(lng), "city": city, "state": state}
