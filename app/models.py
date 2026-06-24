"""ORM models. Maps the contract's data model onto SQLAlchemy.

For a custom API the contract drops the Supabase-specific pieces: auth is an
app-owned `users` table (argon2 hash), authorization is enforced in the API
layer, and storage keys (`storage_path`) live on a `photos` table while the
bytes sit in an S3-compatible bucket.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text,
)
from sqlalchemy.orm import relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Contractor(Base):
    __tablename__ = "contractors"
    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    registration_no = Column(String)
    contractor_number = Column(String, index=True)   # EMC field number, e.g. EELW01 (added: identity step)
    active = Column(Boolean, default=True)


class AccessCredential(Base):
    """Field-login type-gates (NOT people). Each row is one shared password for a
    type/contractor. EMP -> one row (no contractor). EMC -> one row per contractor,
    disambiguated by which password matches. Rotation at contract end = replace the
    hash (or set active=False) on one row; no app/user changes. (added: identity step)
    """
    __tablename__ = "access_credentials"
    id = Column(String, primary_key=True, default=_uuid)
    kind = Column(String, nullable=False)                          # 'EMP' | 'EMC'
    label = Column(String)                                         # human note, e.g. 'EMP staff', 'EMC Nuevo'
    password_hash = Column(String, nullable=False)                 # argon2
    contractor_id = Column(String, ForeignKey("contractors.id"))   # EMC only; NULL for EMP
    active = Column(Boolean, default=True)

    contractor = relationship("Contractor")


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=_uuid)
    employee_id = Column(String, unique=True, nullable=False, index=True)
    full_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="installer")  # installer|supervisor|auditor|admin
    contractor_id = Column(String, ForeignKey("contractors.id"))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    contractor = relationship("Contractor")


class AssetType(Base):
    __tablename__ = "asset_types"
    id = Column(String, primary_key=True, default=_uuid)
    code = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    ocr_fields = Column(JSON, default=list)  # ["imei","serial_no",...]


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id = Column(String, primary_key=True, default=_uuid)
    work_order_no = Column(String, nullable=False, index=True)
    jobcart_ref = Column(String)
    expected_quantity = Column(Integer, default=0)
    status = Column(String, default="open")  # open|closed
    contractor_id = Column(String, ForeignKey("contractors.id"))


class Asset(Base):
    __tablename__ = "assets"
    id = Column(String, primary_key=True, default=_uuid)
    client_uuid = Column(String, index=True)  # idempotency key for batch replay
    asset_type_code = Column(String, ForeignKey("asset_types.code"), index=True)
    work_order_id = Column(String, ForeignKey("work_orders.id"))
    installer_id = Column(String, ForeignKey("users.id"), index=True)
    contractor_id = Column(String, ForeignKey("contractors.id"), index=True)

    serial_no = Column(String, index=True)
    imei = Column(String, index=True)
    manufacturer = Column(String)            # luminaire manufacturer (nameplate OCR)
    model_no = Column(String)                # luminaire model (nameplate OCR) [added: luminaire register]
    manufacture_year = Column(Integer)       # luminaire year
    wattage = Column(Integer)                # luminaire wattage
    controller_manufacturer = Column(String) # smart controller manufacturer (controller OCR) [added]
    controller_model = Column(String)        # smart controller model (controller OCR) [added]
    # NOTE: the `imei` column above carries the CONTROLLER IMEI for luminaire captures.
    pole_no = Column(String)
    work_order_no = Column(String)           # free-text WO-####### (replaces server picker) [added]
    region = Column(String)                  # eThekwini operational region [added]

    lat = Column(Float)
    lng = Column(Float)
    accuracy_m = Column(Float)
    road = Column(String)
    suburb = Column(String)
    city = Column(String)              # legacy: pre-Option-2 captures only; new captures use town/municipality
    town = Column(String)             # populated place, e.g. uMhlanga (Google locality) [added: location levels]
    municipality = Column(String)     # metro/district, e.g. eThekwini Metropolitan Municipality (admin_area_level_2) [added]

    notes = Column(Text)
    damage_flag = Column(Boolean, default=False)
    corrosion_flag = Column(Boolean, default=False)
    device_id = Column(String)
    ocr_source = Column(String)             # ai|fallback|manual
    extracted = Column(JSON, default=dict)  # raw OCR bag

    status = Column(String, default="pending")  # pending|synced|...
    captured_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_now)
    deleted_at = Column(DateTime(timezone=True))  # soft delete

    # --- capturer identity (added: identity step) -------------------------
    # Stamped per-capture. *_kind/company_name/contractor_number/section come
    # from the field TOKEN (server-trusted); name/surname/service_no/designation
    # are the self-declared typed fields. installer_id stays NULL for field captures.
    captured_by_kind = Column(String)       # 'EMP' | 'EMC'
    captured_by_name = Column(String)
    captured_by_surname = Column(String)
    service_no = Column(String)             # EMP only, validated xxxxx-xx
    designation = Column(String)            # EMP: Clerk of Works/Superintendent; EMC: fixed Electrician
    section = Column(String)                # EMP: fixed 'Lighting Works'
    company_name = Column(String)           # EMC, from token (contractor name)
    contractor_number = Column(String)      # EMC, from token (e.g. EELW01)

    installer = relationship("User")
    contractor = relationship("Contractor")
    work_order = relationship("WorkOrder")
    photos = relationship("Photo", back_populates="asset", cascade="all, delete-orphan")


class Photo(Base):
    __tablename__ = "photos"
    id = Column(String, primary_key=True, default=_uuid)
    asset_id = Column(String, ForeignKey("assets.id"))
    kind = Column(String)          # plate|overall|identity
    storage_path = Column(String)
    created_at = Column(DateTime(timezone=True), default=_now)

    asset = relationship("Asset", back_populates="photos")


class FactoryUnit(Base):
    """Manufacturer 'units made' feed — lets installed assets be matched."""
    __tablename__ = "factory_units"
    id = Column(String, primary_key=True, default=_uuid)
    manufacturer = Column(String)
    serial_no = Column(String, index=True)
    imei = Column(String, index=True)
    batch_ref = Column(String)
    manufactured_at = Column(DateTime(timezone=True))
    raw = Column(JSON, default=dict)


class Audit(Base):
    __tablename__ = "audits"
    id = Column(String, primary_key=True, default=_uuid)
    asset_id = Column(String, ForeignKey("assets.id"))
    status = Column(String, default="open")  # open|passed|failed
    findings = Column(Text)
    audited_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_now)
