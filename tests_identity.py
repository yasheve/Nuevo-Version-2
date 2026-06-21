"""Isolated backend harness for the EMP/EMC identity step (§8).

Runs the ACTUAL shipped app over a fresh SQLite DB + known FIELD_PW_* env.
Stamped capturer columns are not exposed by the API, so they are asserted
directly against the stored Asset row. No network, no real storage calls.
"""
import os
import sys
import tempfile
import shutil

TMP = tempfile.mkdtemp(prefix="nuevo_ident_")
os.environ.update({
    "DATABASE_URL": f"sqlite:///{TMP}/test.db",
    "STORAGE_DIR": f"{TMP}/storage",
    "SEED_ON_START": "true",
    "SEED_DEMO_PASSWORD": "nuevo123",     # back-office/admin password
    "FIELD_PW_EMP": "emp-pass",
    "FIELD_PW_EMC1": "emc1-pass",
    "FIELD_PW_EMC2": "emc2-pass",
    "FIELD_PW_EMC3": "emc3-pass",
    "JWT_SECRET": "test-secret",
    "CORS_ORIGINS": "*",
    # shipped storage.py is the S3 backend and validates these at import; the
    # harness exercises NO storage calls (captures have no photos, soft-delete
    # touches no bytes), so dummy values just let the real app boot offline.
    "S3_BUCKET": "test-bucket",
    "AWS_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
})

BACKEND = "/home/claude/work/NuEvo_Identity_Onboarding_Handover/backend"
sys.path.insert(0, BACKEND)

import jwt as _jwt
from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models import Asset, AccessCredential
from app.security import hash_password

NAV = "Nuevo Sales and Marketing (Pty) Ltd"
ECO = "EcoSolutions Projects Management (Pty) Ltd"
ABC = "ABC (Pty) Ltd"

_p = _f = 0
def ok(cond, name):
    global _p, _f
    if cond:
        _p += 1; print(f"  PASS  {name}")
    else:
        _f += 1; print(f"  FAIL  {name}")

def claims(tok):
    return _jwt.decode(tok, "test-secret", algorithms=["HS256"])

def field_login(c, kind, pw):
    return c.post("/v1/auth/field-login", json={"kind": kind, "password": pw})

def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}

def last_asset_by_serial(serial):
    db = SessionLocal()
    try:
        return db.query(Asset).filter(Asset.serial_no == serial).first()
    finally:
        db.close()

_serial = [0]
def serial():
    _serial[0] += 1
    return f"SN-IDENT-{_serial[0]:04d}"


with TestClient(app) as c:
    print("\n[1] field-login + EMC password->identity mapping")
    r = field_login(c, "EMP", "emp-pass")
    ok(r.status_code == 200, "EMP login 200")
    emp_tok = r.json().get("token", "")
    cl = claims(emp_tok)
    ok(cl.get("kind") == "EMP" and "contractor_number" not in cl, "EMP token: kind=EMP, no contractor claims")

    emc_tok = {}
    for n, pw, num, name in [(1, "emc1-pass", "EELW01", NAV),
                             (2, "emc2-pass", "EELW02", ECO),
                             (3, "emc3-pass", "EELW03", ABC)]:
        r = field_login(c, "EMC", pw)
        j = r.json()
        emc_tok[n] = j.get("token", "")
        cl = claims(emc_tok[n])
        a = j.get("actor", {})
        ok(r.status_code == 200 and cl.get("contractor_number") == num and cl.get("company_name") == name,
           f"EMC#{n} token -> {num} / {name}")
        ok(a.get("contractor_number") == num and a.get("company_name") == name,
           f"EMC#{n} response.actor -> {num} / {name}")

    print("\n[2] wrong password / unknown kind -> 401")
    ok(field_login(c, "EMP", "nope").status_code == 401, "wrong EMP password 401")
    ok(field_login(c, "EMC", "nope").status_code == 401, "wrong EMC password 401")
    ok(field_login(c, "XYZ", "emp-pass").status_code == 401, "unknown kind 401")

    print("\n[3] rotation: replace one EMC hash; old fails, new works, others unaffected")
    db = SessionLocal()
    cred = db.query(AccessCredential).filter(AccessCredential.label == "EMC Nuevo").first()
    cred.password_hash = hash_password("rotated-emc1")
    db.commit(); db.close()
    ok(field_login(c, "EMC", "emc1-pass").status_code == 401, "old EMC#1 password now 401")
    r = field_login(c, "EMC", "rotated-emc1")
    ok(r.status_code == 200 and claims(r.json()["token"]).get("contractor_number") == "EELW01",
       "new EMC#1 password works -> EELW01")
    ok(field_login(c, "EMC", "emc2-pass").status_code == 200, "EMC#2 unaffected by rotation")

    print("\n[4] deactivation: active=False -> password 401")
    db = SessionLocal()
    cred = db.query(AccessCredential).filter(AccessCredential.label == "EMC EcoSolutions").first()
    cred.active = False
    db.commit(); db.close()
    ok(field_login(c, "EMC", "emc2-pass").status_code == 401, "deactivated EMC#2 now 401")

    print("\n[5] POST /assets (EMP token): server-side validation = the real gate")
    base = lambda **kw: {"serial_no": serial(), "captured_by_name": "A", "captured_by_surname": "B", **kw}
    # missing service_no
    ok(c.post("/v1/assets", headers=bearer(emp_tok),
              json=base(designation="Clerk of Works (Electrical)")).status_code == 422,
       "EMP missing service_no -> 422")
    for bad in ["1234-56", "123456-7", "abcde-12", "12345-6", "12345-678"]:
        ok(c.post("/v1/assets", headers=bearer(emp_tok),
                  json=base(service_no=bad, designation="Clerk of Works (Electrical)")).status_code == 422,
           f"EMP malformed service_no {bad!r} -> 422")
    # designation outside the two
    ok(c.post("/v1/assets", headers=bearer(emp_tok),
              json=base(service_no="12345-67", designation="Electrician")).status_code == 422,
       "EMP bad designation -> 422")
    # valid EMP capture; section forced even though body tries to set it
    s_ok = serial()
    r = c.post("/v1/assets", headers=bearer(emp_tok),
               json={"serial_no": s_ok, "captured_by_name": "Thabo", "captured_by_surname": "M",
                     "service_no": "12345-67", "designation": "Superintendent (Electrical)",
                     "section": "HACK-SECTION", "company_name": "HACK", "contractor_number": "HACK",
                     "captured_by_kind": "EMC"})
    ok(r.status_code == 201, "EMP valid service_no/designation -> 201")
    a = last_asset_by_serial(s_ok)
    ok(a.captured_by_kind == "EMP", "EMP stored kind=EMP (body 'EMC' ignored)")
    ok(a.section == "Lighting Works", "EMP section forced 'Lighting Works' (body ignored)")
    ok(a.designation == "Superintendent (Electrical)", "EMP designation stored")
    ok(a.service_no == "12345-67", "EMP service_no stored")
    ok(a.company_name is None and a.contractor_number is None, "EMP has no company/number")
    ok(a.installer_id is None, "field capture installer_id is NULL")

    print("\n[6] POST /assets (EMC token): identity from TOKEN, never the body")
    s_emc = serial()
    r = c.post("/v1/assets", headers=bearer(emc_tok[1]),
               json={"serial_no": s_emc, "captured_by_name": "Yash", "captured_by_surname": "N",
                     "designation": "Manager", "company_name": "HACK CO",
                     "contractor_number": "HACK99", "section": "HACK"})
    ok(r.status_code == 201, "EMC valid capture -> 201")
    a = last_asset_by_serial(s_emc)
    ok(a.company_name == NAV, "EMC company_name from token (body 'HACK CO' ignored)")
    ok(a.contractor_number == "EELW01", "EMC contractor_number from token (body ignored)")
    ok(a.designation == "Electrician", "EMC designation forced 'Electrician' (body ignored)")
    ok(a.captured_by_kind == "EMC", "EMC stored kind=EMC")

    print("\n[7] batch: per-item validation; one bad item doesn't sink the batch")
    body = {"items": [
        {"client_uuid": "u-good", "serial_no": serial(), "captured_by_name": "G", "captured_by_surname": "G",
         "service_no": "55555-01", "designation": "Clerk of Works (Electrical)"},
        {"client_uuid": "u-bad", "serial_no": serial(), "captured_by_name": "X", "captured_by_surname": "X",
         "designation": "Clerk of Works (Electrical)"},  # missing service_no
    ]}
    r = c.post("/v1/assets/batch", headers=bearer(emp_tok), json=body)
    ok(r.status_code == 200, "batch -> 200 (not 500)")
    res = {x["client_uuid"]: x for x in r.json()["results"]}
    ok(res["u-good"]["status"] == "created", "batch good item created")
    ok(res["u-bad"]["status"] == "error", "batch bad item error (batch not sunk)")

    print("\n[8] field tokens reach the entry/read endpoints (the gap fix)")
    ok(c.get("/v1/asset-types", headers=bearer(emp_tok)).status_code == 200, "GET /asset-types with field token 200")
    ok(c.get("/v1/contractors", headers=bearer(emp_tok)).status_code == 200, "GET /contractors with field token 200")
    ok(c.get("/v1/work-orders", headers=bearer(emc_tok[1])).status_code == 200, "GET /work-orders with field token 200")
    ok(c.get("/v1/assets", headers=bearer(emp_tok)).status_code == 200, "GET /assets (list) with field token 200")
    s_tr = serial()
    c.post("/v1/assets", headers=bearer(emp_tok),
           json=base(serial_no=s_tr, service_no="12345-67", designation="Clerk of Works (Electrical)"))
    ok(c.get(f"/v1/trace?serial={s_tr}", headers=bearer(emp_tok)).status_code == 200, "GET /trace with field token 200")

    print("\n[9] admin/back-office path intact; delete still admin-only")
    r = c.post("/v1/auth/login", json={"employee_id": "EMP-0001", "password": "nuevo123"})
    ok(r.status_code == 200 and r.json()["user"]["role"] == "admin", "back-office /auth/login (admin) 200")
    admin_tok = r.json()["token"]
    # admin capture: no identity fields, is_field False -> 201, installer_id set
    s_adm = serial()
    r = c.post("/v1/assets", headers=bearer(admin_tok), json={"serial_no": s_adm})
    ok(r.status_code == 201, "admin (back-office) capture still works -> 201 (additive)")
    a = last_asset_by_serial(s_adm)
    ok(a.installer_id is not None and a.captured_by_kind is None, "admin capture: installer_id set, no capturer-identity")
    # delete: field token denied, admin allowed
    aid = r.json()["id"]
    ok(c.delete(f"/v1/assets/{aid}", headers=bearer(emp_tok)).status_code in (401, 403), "field token DELETE denied")
    ok(c.delete(f"/v1/assets/{aid}", headers=bearer(admin_tok)).status_code == 200, "admin DELETE allowed")

print(f"\n================  {_p} passed, {_f} failed  ================")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _f else 0)
