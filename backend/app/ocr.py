"""Server-side nameplate OCR.

The model key lives here, never in the app. This always returns HTTP 200: on
any problem (no key configured, model unreachable, unparseable output) it
returns source="fallback" with empty fields so the installer types them in.
Set ANTHROPIC_API_KEY to turn on live extraction.
"""
import base64
import json

from .config import settings
from .storage import read_bytes

_GUESS_MEDIA = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png"}


def _media_type(data: bytes) -> str:
    for sig, mt in _GUESS_MEDIA.items():
        if data.startswith(sig):
            return mt
    return "image/jpeg"


def _load_image(storage_path: str | None, image_base64: str | None) -> bytes | None:
    if image_base64:
        try:
            return base64.b64decode(image_base64)
        except Exception:
            return None
    if storage_path:
        return read_bytes(storage_path)
    return None


def run_ocr(asset_type_code: str, ocr_fields: list[str],
            storage_path: str | None, image_base64: str | None) -> dict:
    fallback = {"source": "fallback", "fields": {}}
    if not settings.ANTHROPIC_API_KEY:
        return fallback

    data = _load_image(storage_path, image_base64)
    if not data:
        return fallback

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        fields_csv = ", ".join(ocr_fields) if ocr_fields else "serial_no, imei, manufacturer, manufacture_year"
        prompt = (
            f"You are reading the equipment nameplate of a '{asset_type_code}'. "
            f"Extract ONLY these fields: {fields_csv}. "
            "Return STRICT JSON with exactly those keys, no prose, no markdown. "
            "Use null for any field you cannot read with confidence. "
            "manufacture_year must be an integer if present."
        )
        msg = client.messages.create(
            model=settings.OCR_MODEL,
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": _media_type(data),
                        "data": base64.b64encode(data).decode(),
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return fallback
        clean = {k: v for k, v in parsed.items() if v not in (None, "", "null")}
        return {"source": "ai", "fields": clean}
    except Exception:
        return fallback
