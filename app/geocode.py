"""Server-side reverse geocoding (Geoapify or Google; select via GEOCODE_PROVIDER).

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


def _g_component(components: list, *types: str) -> str:
    """First Google address-component long_name matching one of `types`.

    Google returns `address_components` as a flat list, each tagged with one or
    more `types`. We scan in the caller's PRIORITY order: for each desired type
    in turn, return the long_name of the first component carrying it. This lets
    the caller control precedence (e.g. sublocality_level_1 before neighborhood).
    """
    for t in types:
        for comp in components:
            if t in (comp.get("types") or []):
                name = comp.get("long_name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    return ""


# Municipality -> preferred register city name. Google returns the LEGAL
# municipality at administrative_area_level_2 (e.g. "eThekwini Metropolitan
# Municipality"); the register wants the common city name. The match is a
# case-insensitive substring on a distinctive token, so every name variant
# ("eThekwini", "eThekwini Metropolitan Municipality") resolves the same.
# ROLLOUT: add one (token, display) pair per province as the app expands.
# Unmapped municipalities pass through unchanged, so a new area is never blank.
_CITY_ALIASES = (
    ("ethekwini", "Durban"),
    # ("city of cape town", "Cape Town"),
    # ("city of johannesburg", "Johannesburg"),
    # ("city of tshwane", "Pretoria"),
    # ("nelson mandela bay", "Gqeberha"),
)


def _norm_city(name: str) -> str:
    low = (name or "").lower()
    for token, display in _CITY_ALIASES:
        if token in low:
            return display
    return name


def _city(components: list) -> str:
    """Register 'City' = the municipality, normalized to its common name.

    Reads administrative_area_level_2 (the metro/district municipality) so every
    suburb and town inside a metro reports ONE city — e.g. a pin in La Lucia /
    uMhlanga reports the eThekwini metro (-> "Durban"), not the local town. Falls
    back to locality -> postal_town only when the municipality level is absent
    (some rural / informal points), so the field is never needlessly blank.
    """
    raw = _g_component(components, "administrative_area_level_2", "locality", "postal_town")
    return _norm_city(raw)


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


def _google(lat: float, lng: float) -> dict:
    # Google Geocoding API (v3 web service). latlng MUST be "lat,lng" with no
    # space between the values; urlencode percent-encodes the comma, which the
    # API accepts. language=en keeps names in English (eThekwini house language).
    # Google returns no per-result distance from the query point, so distance_m
    # is null here (the response plus_code is guaranteed within 10 m of the point
    # but is not surfaced).
    qs = urllib.parse.urlencode({
        "latlng": "%s,%s" % (lat, lng),
        "key": settings.GEOCODE_API_KEY,
        "language": "en",
    })
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + qs
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    # `status` is the authoritative success flag. Anything other than OK
    # (ZERO_RESULTS, OVER_QUERY_LIMIT, REQUEST_DENIED, INVALID_REQUEST,
    # UNKNOWN_ERROR) -> empty fields, so the capture form falls back to manual
    # entry (mirrors the no-key / unreachable paths).
    if payload.get("status") != "OK":
        return _empty()
    results = payload.get("results") or []
    if not results:
        return _empty()
    comps = results[0].get("address_components") or []
    # [CHECK] SA type mapping below follows Google's documented address-component
    # types; confirm against a live Durban response on the first real capture
    # (informal / new areas may populate fewer of these). Note: Google spells it
    # "neighborhood" (US), unlike Geoapify's "neighbourhood".
    return {
        "road":   _g_component(comps, "route"),
        "suburb": _g_component(comps, "sublocality_level_1", "sublocality", "neighborhood"),
        "city":   _city(comps),
        "distance_m": None,
        "source": "google",
    }


def reverse_geocode(lat: float, lng: float) -> dict:
    """lat/lng -> {road, suburb, city, distance_m, source}. Never raises."""
    if not settings.GEOCODE_API_KEY:
        return _empty()
    try:
        provider = (settings.GEOCODE_PROVIDER or "geoapify").strip().lower()
        if provider == "geoapify":
            return _geoapify(lat, lng)
        if provider == "google":
            return _google(lat, lng)
        return _empty()
    except Exception:
        return _empty()
