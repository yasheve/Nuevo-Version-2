"""Server-side reverse geocoding (Geoapify by default).

The provider key lives here, never in the app. This always returns HTTP 200:
on any problem (no key configured, provider unreachable, unparseable output)
it returns source="none" with empty fields so the installer types the address
in by hand (mirrors app/ocr.py). Set GEOCODE_API_KEY to turn on live address
auto-fill from the device's GPS fix.

No third-party SDK and no new dependency: one GET via the Python standard
library. Swap providers by changing GEOCODE_PROVIDER + GEOCODE_API_KEY — the
PWA never changes, it only ever calls /v1/geocode.
"""
import json
import urllib.parse
import urllib.request

from .config import settings

_TIMEOUT_S = 8


def _empty() -> dict:
    return {"road": "", "suburb": "", "city": "", "distance_m": None, "source": "none"}


def _first(d: dict, *keys: str) -> str:
    """First non-empty string value among `keys` in a result object."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _geoapify(lat: float, lng: float) -> dict:
    # Reverse geocode a single point. format=json -> flat `results[0]` keys;
    # lang=en keeps street/suburb/city names in English (eThekwini house
    # language). The response also carries `distance` (metres from the input
    # point) which we surface as distance_m for a future sanity check.
    qs = urllib.parse.urlencode({
        "lat": lat,
        "lon": lng,
        "apiKey": settings.GEOCODE_API_KEY,
        "lang": "en",
        "format": "json",
        "limit": 1,
    })
    url = "https://api.geoapify.com/v1/geocode/reverse?" + qs
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    results = payload.get("results") or []
    if not results:
        return _empty()
    r = results[0]
    dist = r.get("distance")
    # [CHECK] Geoapify result keys below follow the documented schema; confirm
    # the mapping against a live Durban response with a real key on the first
    # real capture (informal / new areas may populate fewer of these).
    return {
        "road":   _first(r, "street", "name"),
        "suburb": _first(r, "suburb", "district", "neighbourhood", "quarter"),
        "city":   _first(r, "city", "town", "village", "county"),
        "distance_m": (round(float(dist), 1) if isinstance(dist, (int, float)) else None),
        "source": "geoapify",
    }


def reverse_geocode(lat: float, lng: float) -> dict:
    """lat/lng -> {road, suburb, city, distance_m, source}. Never raises."""
    if not settings.GEOCODE_API_KEY:
        return _empty()
    try:
        provider = (settings.GEOCODE_PROVIDER or "geoapify").strip().lower()
        if provider == "geoapify":
            return _geoapify(lat, lng)
        return _empty()
    except Exception:
        return _empty()
