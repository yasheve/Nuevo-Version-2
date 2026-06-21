"""Seed reference data + demo accounts on first run (idempotent).

Demo password for every seeded account = settings.SEED_DEMO_PASSWORD (default
'nuevo123'). Change or disable via env in production (SEED_ON_START=false).

Asset types are handled separately (see ensure_asset_types): they are topped up
on EVERY boot, so adding a new capture type only needs the matching code added to
ASSET_TYPES below plus a redeploy -- no manual database edits, and it self-heals
even after the initial seed has already run.
"""
from datetime import datetime, timezone
import os

from sqlalchemy.orm import Session

from .config import settings
from .models import (AccessCredential, AssetType, Audit, Contractor,
                     FactoryUnit, User, WorkOrder)
from .security import hash_password

DEMO_USERS = [
    ("EMP-0001", "S. Administrator", "admin", None),
    ("EMP-1043", "A. Naidoo", "installer", "A"),
    ("EMP-2001", "P. Supervisor", "supervisor", "A"),
    ("EMP-3001", "T. Auditor", "auditor", None),
]


# Every asset type the field app can capture. These codes MUST match the app's
# ASSETS keys in index.html. `assets.asset_type_code` is a foreign key to
# `asset_types.code`, so any type the app sends that is missing here is rejected
# and the record will not sync. To add a capture type, add it in BOTH places.
ASSET_TYPES = [
    ("streetlight_controller", "Streetlight controller",
     ["imei", "serial_no", "manufacturer", "manufacture_year"]),
    ("streetlight_luminaire", "Streetlight luminaire",
     ["serial_no", "manufacturer", "wattage", "manufacture_year"]),
    ("mini_substation", "Mini substation",
     ["serial_no", "manufacturer", "manufacture_year"]),
    ("ring_main_unit", "Ring main unit",
     ["serial_no", "manufacturer", "manufacture_year"]),
    ("meter", "Electricity meter",
     ["serial_no", "manufacturer", "manufacture_year"]),
    ("transformer", "Transformer",
     ["serial_no", "manufacturer", "manufacture_year"]),
    ("cable_joint", "Cable joint / termination",
     ["serial_no", "manufacturer", "manufacture_year"]),
]


def ensure_asset_types(db: Session) -> None:
    """Idempotently make sure every asset type the app can capture exists.

    Runs on every boot (called from seed() before the one-time gate below), so it
    tops up any missing rows even after the database has already been seeded.
    Existing rows are left untouched.
    """
    existing = {a.code for a in db.query(AssetType).all()}
    added = False
    for code, name, fields in ASSET_TYPES:
        if code not in existing:
            db.add(AssetType(code=code, name=name, ocr_fields=fields))
            added = True
    if added:
        db.commit()


def seed(db: Session) -> None:
    # Always keep asset types in sync with the app, even after the first seed.
    ensure_asset_types(db)

    if db.query(User).count() > 0:
        return  # already seeded

    pw = hash_password(settings.SEED_DEMO_PASSWORD)

    # Field-login passwords come from env at deploy (FIELD_PW_*); never literals.
    # The dev fallback keeps the three EMC passwords DISTINCT so each resolves to its
    # own contractor (required for the 1 password <-> 1 contractor mapping to be testable).
    demo = settings.SEED_DEMO_PASSWORD
    field_pw = {
        "EMP":  os.getenv("FIELD_PW_EMP")  or f"{demo}-emp",
        "EMC1": os.getenv("FIELD_PW_EMC1") or f"{demo}-emc1",
        "EMC2": os.getenv("FIELD_PW_EMC2") or f"{demo}-emc2",
        "EMC3": os.getenv("FIELD_PW_EMC3") or f"{demo}-emc3",
    }

    contractors = {
        # Names + numbers per the identity-step spec (§3). Spelling is exact:
        # "Nuevo" (lowercase e), "and" spelled out.
        "A": Contractor(name="Nuevo Sales and Marketing (Pty) Ltd",
                        registration_no="2019/013435/07", contractor_number="EELW01"),
        "B": Contractor(name="EcoSolutions Projects Management (Pty) Ltd",
                        contractor_number="EELW02"),
        # ABC (Pty) Ltd is a DEMO PLACEHOLDER — replace with the real third
        # contractor (and its number if different) before any production use.
        "C": Contractor(name="ABC (Pty) Ltd", contractor_number="EELW03"),
    }
    for c in contractors.values():
        db.add(c)
    db.flush()

    for emp, name, role, ckey in DEMO_USERS:
        db.add(User(employee_id=emp, full_name=name, role=role,
                    password_hash=pw,
                    contractor_id=contractors[ckey].id if ckey else None))

    # Field-login credentials: 1 EMP (no contractor) + 3 EMC (one per contractor).
    # Rotation at contract end = replace one hash or set active=False; no app redeploy.
    db.add(AccessCredential(kind="EMP", label="EMP staff (Lighting Works)",
                            password_hash=hash_password(field_pw["EMP"])))
    db.add(AccessCredential(kind="EMC", label="EMC Nuevo",
                            password_hash=hash_password(field_pw["EMC1"]),
                            contractor_id=contractors["A"].id))
    db.add(AccessCredential(kind="EMC", label="EMC EcoSolutions",
                            password_hash=hash_password(field_pw["EMC2"]),
                            contractor_id=contractors["B"].id))
    db.add(AccessCredential(kind="EMC", label="EMC ABC (placeholder)",
                            password_hash=hash_password(field_pw["EMC3"]),
                            contractor_id=contractors["C"].id))

    work_orders = [
        ("WO-2291", "JC-88", 40, "open", "A"),
        ("WO-2292", "JC-91", 25, "open", "A"),
        ("WO-2310", "JC-104", 60, "open", "B"),
    ]
    for no, jc, qty, status, ckey in work_orders:
        db.add(WorkOrder(work_order_no=no, jobcart_ref=jc, expected_quantity=qty,
                         status=status, contractor_id=contractors[ckey].id))

    # A small factory feed so /trace can demonstrate factory_match=true.
    db.add(FactoryUnit(manufacturer="Wuxi", serial_no="SN-99381", imei="356938035643809",
                       batch_ref="B-22", manufactured_at=datetime(2026, 5, 2, tzinfo=timezone.utc)))

    db.commit()
