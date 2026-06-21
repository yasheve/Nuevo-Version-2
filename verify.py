"""Drives the full app<->API loop against a running server and prints PASS/FAIL."""
import base64
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000/v1"

# valid 1x1 JPEG
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAAR"
    "CAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAA"
    "AAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oA"
    "DAMBAAIRAxEAPwCdABmX/9k=")

PASS, FAIL = [], []


def call(method, path, token=None, body=None, raw=None, full_url=None, ctype=None):
    url = full_url or (BASE + path)
    data = None
    headers = {}
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            ct = r.headers.get("Content-Type", "")
            payload = r.read()
            return r.status, (json.loads(payload) if ct.startswith("application/json") else payload)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


# 1. health (public)
s, j = call("GET", "/health")
check("GET /health 200 + status ok", s == 200 and j.get("status") == "ok", f"{s} {j}")

# 2. login bad creds -> 401 invalid_credentials
s, j = call("POST", "/auth/login", body={"employee_id": "EMP-1043", "password": "wrong"})
check("login wrong password -> 401 invalid_credentials",
      s == 401 and j.get("error", {}).get("code") == "invalid_credentials", f"{s}")

# 3. login good
s, j = call("POST", "/auth/login", body={"employee_id": "EMP-1043", "password": "nuevo123"})
ok = s == 200 and "token" in j and j["user"]["role"] == "installer"
check("login -> token + installer user", ok, f"{s} role={j.get('user',{}).get('role')}")
token = j["token"]
contractor_id = j["user"]["contractor_id"]

# 4. me
s, j = call("GET", "/auth/me", token=token)
check("GET /auth/me", s == 200 and j["full_name"] == "A. Naidoo", f"{s}")

# 5. unauth call -> 401
s, j = call("GET", "/assets")
check("GET /assets without token -> 401", s == 401, f"{s}")

# 6. reference
s, at = call("GET", "/asset-types", token=token)
codes = [x["code"] for x in at] if isinstance(at, list) else []
check("GET /asset-types includes streetlight_controller",
      s == 200 and "streetlight_controller" in codes, f"{s} {codes}")
s, co = call("GET", "/contractors", token=token)
check("GET /contractors", s == 200 and len(co) >= 1, f"{s} n={len(co) if isinstance(co,list) else '?'}")
s, wo = call("GET", f"/work-orders?contractor_id={contractor_id}", token=token)
check("GET /work-orders (scoped)", s == 200 and len(wo) >= 1, f"{s} n={len(wo) if isinstance(wo,list) else '?'}")
work_order_id = wo[0]["id"] if wo else None

# 7. presign
s, pre = call("POST", "/uploads/presign", token=token,
              body={"kind": "plate", "content_type": "image/jpeg"})
ok = s == 200 and pre.get("upload_url") and pre.get("storage_path")
check("POST /uploads/presign -> upload_url + storage_path", ok, f"{s}")
upload_url, storage_path = pre["upload_url"], pre["storage_path"]

# 8. PUT bytes straight to storage (no bearer; signature in URL)
s, j = call("PUT", None, full_url=upload_url, raw=JPEG, ctype="image/jpeg")
check("PUT bytes to presigned URL -> 200", s == 200, f"{s} {j}")

# 9. OCR (no key -> graceful fallback, still 200)
s, j = call("POST", "/ocr", token=token,
            body={"asset_type_code": "streetlight_controller",
                  "image_base64": base64.b64encode(JPEG).decode()})
check("POST /ocr always 200 with source", s == 200 and j.get("source") in ("ai", "fallback"),
      f"{s} source={j.get('source')}")

# 10. create asset referencing the uploaded photo
asset_body = {
    "asset_type_code": "streetlight_controller", "work_order_id": work_order_id,
    "serial_no": "SN-99381", "imei": "356938035643809", "manufacturer": "Wuxi",
    "manufacture_year": 2025,
    "extracted": {"serial_no": "SN-99381"}, "ocr_source": "fallback",
    "pole_no": "P-4471",
    "location": {"lat": -29.8587, "lng": 31.0218, "accuracy_m": 6.0},
    "address": {"road": "Smith St", "suburb": "Durban CBD", "city": "Durban"},
    "notes": "Mounted, no visible damage", "damage_flag": False, "corrosion_flag": False,
    "device_id": "PHONE-ABC-123", "captured_at": "2026-06-17T08:31:10Z",
    "photos": [{"kind": "plate", "storage_path": storage_path, "key": "imei"}],  # extra 'key' must be tolerated
}
s, j = call("POST", "/assets", token=token, body=asset_body)
ok = s == 201 and j.get("status") == "pending" and j.get("id")
check("POST /assets -> 201 pending (tolerates extra photo key)", ok, f"{s} {j}")
asset_id = j.get("id")

# 11. duplicate -> 409
s, j = call("POST", "/assets", token=token, body=asset_body)
check("POST /assets duplicate -> 409 duplicate_unit",
      s == 409 and j.get("error", {}).get("code") == "duplicate_unit", f"{s}")

# 12. list
s, j = call("GET", "/assets?limit=50", token=token)
items = j.get("items", []) if isinstance(j, dict) else []
check("GET /assets -> items[] with our record",
      s == 200 and any(i["id"] == asset_id for i in items), f"{s} n={len(items)}")

# 13. detail + signed photo
s, j = call("GET", f"/assets/{asset_id}", token=token)
photos = j.get("photos", []) if isinstance(j, dict) else []
signed = photos[0]["url"] if photos else None
check("GET /assets/{id} -> detail + signed photo url", s == 200 and signed, f"{s} photos={len(photos)}")

# 14. fetch the signed image (no bearer)
if signed:
    s, blob = call("GET", None, full_url=signed)
    check("GET signed image url -> bytes returned",
          s == 200 and isinstance(blob, (bytes, bytearray)) and len(blob) > 0,
          f"{s} bytes={len(blob) if isinstance(blob,(bytes,bytearray)) else '?'}")

# 15. trace -> factory_match true (factory feed seeded)
s, j = call("GET", "/trace?serial=SN-99381", token=token)
check("GET /trace -> asset + factory_match true",
      s == 200 and j.get("asset", {}).get("serial_no") == "SN-99381" and j.get("factory_match") is True,
      f"{s} factory_match={j.get('factory_match')}")

# 16. batch: one new + one duplicate, idempotent
batch_body = {"items": [
    {"client_uuid": "c-NEW-1", "asset_type_code": "meter", "serial_no": "MTR-700001",
     "work_order_id": work_order_id, "captured_at": "2026-06-17T09:00:00Z"},
    {"client_uuid": "c-DUP-1", "asset_type_code": "streetlight_controller",
     "serial_no": "SN-99381", "captured_at": "2026-06-17T09:01:00Z"},
]}
s, j = call("POST", "/assets/batch", token=token, body=batch_body)
res = {r.get("client_uuid"): r.get("status") for r in j.get("results", [])} if isinstance(j, dict) else {}
check("POST /assets/batch -> created + duplicate",
      s == 200 and res.get("c-NEW-1") == "created" and res.get("c-DUP-1") == "duplicate", f"{s} {res}")

# 17. batch replay is idempotent (same client_uuid -> created, no new dup row)
s, j2 = call("POST", "/assets/batch", token=token,
             body={"items": [batch_body["items"][0]]})
res2 = {r.get("client_uuid"): r.get("status") for r in j2.get("results", [])} if isinstance(j2, dict) else {}
check("POST /assets/batch replay idempotent", s == 200 and res2.get("c-NEW-1") == "created", f"{s} {res2}")

print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
if FAIL:
    print("FAILURES:", FAIL)
    raise SystemExit(1)
