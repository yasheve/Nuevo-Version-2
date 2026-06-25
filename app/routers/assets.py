from datetime import datetime, timezone
import io
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import err
from ..models import Asset, Photo, FactoryUnit
from ..security import (actor_sees_all, can_see_all, get_current_actor,
                        get_current_user, require_role)
from ..storage import signed_get_url, delete_bytes
from .. import register

router = APIRouter()


# ----------------------------- input models -----------------------------
class LocationIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    lat: float | None = None
    lng: float | None = None
    accuracy_m: float | None = None


class AddressIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    road: str | None = None
    suburb: str | None = None
    city: str | None = None              # legacy; tolerated but no longer sent by the PWA
    town: str | None = None              # [added: location levels]
    municipality: str | None = None      # [added: location levels]
    province: str | None = None          # [added: location levels] admin_area_level_1


class PhotoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tolerate extra keys like `key`
    kind: str
    storage_path: str


class AssetIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    asset_type_code: str | None = None
    work_order_id: str | None = None
    serial_no: str | None = None
    imei: str | None = None
    manufacturer: str | None = None
    model_no: str | None = None
    manufacture_year: int | None = None
    wattage: int | None = None
    controller_manufacturer: str | None = None
    controller_model: str | None = None
    extracted: dict | None = None
    ocr_source: str | None = None
    pole_no: str | None = None
    work_order_no: str | None = None
    region: str | None = None
    location: LocationIn | None = None
    address: AddressIn | None = None
    notes: str | None = None
    damage_flag: bool | None = None
    corrosion_flag: bool | None = None
    device_id: str | None = None
    captured_at: str | None = None
    photos: list[PhotoIn] | None = None
    # --- self-declared capturer identity (added: identity step) ----------
    # Only these four are accepted from the body. company_name/contractor_number/
    # kind/section are stamped from the token and CANNOT be set here (extra="ignore"
    # silently drops them if a client tries).
    captured_by_name: str | None = None
    captured_by_surname: str | None = None
    service_no: str | None = None
    designation: str | None = None


class BatchItemIn(AssetIn):
    client_uuid: str | None = None


class BatchIn(BaseModel):
    items: list[BatchItemIn]


# ------------------------------ helpers ----------------------------------
def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _iso(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _find_duplicate(db: Session, serial_no, imei, exclude_id=None):
    q = db.query(Asset).filter(Asset.deleted_at.is_(None))
    conds = []
    if serial_no:
        conds.append(Asset.serial_no == serial_no)
    if imei:
        conds.append(Asset.imei == imei)
    if not conds:
        return None
    from sqlalchemy import or_
    q = q.filter(or_(*conds))
    if exclude_id:
        q = q.filter(Asset.id != exclude_id)
    return q.first()


# --- capturer-identity validation (added: identity step) ----------------
SERVICE_NO_RE = re.compile(r"\d{5}-\d{2}")
EMP_DESIGNATIONS = ("Clerk of Works (Electrical)", "Superintendent (Electrical)")
EMC_DESIGNATION = "Electrician"
EMP_SECTION = "Lighting Works"


def _identity_fields(actor, body: AssetIn) -> dict:
    """Validate + resolve the capturer-identity columns. The SERVER is the real
    gate (front-end gate is UX only). Name/surname are self-declared pass-through;
    EMP service_no/designation are validated and section forced; EMC designation is
    forced and company_name/contractor_number come from the TOKEN actor, never the
    body. Back-office (non-field) captures stamp nothing. Raises 422 on bad input.
    """
    if not getattr(actor, "is_field", False):
        return {}                                        # back-office user capture
    kind = actor.kind
    out = {
        "captured_by_kind": kind,
        "captured_by_name": body.captured_by_name,
        "captured_by_surname": body.captured_by_surname,
    }
    if kind == "EMP":
        sn = (body.service_no or "").strip()
        if not SERVICE_NO_RE.fullmatch(sn):
            raise err(422, "validation", "service_no must match xxxxx-xx (e.g. 12345-67)")
        if body.designation not in EMP_DESIGNATIONS:
            raise err(422, "validation",
                      "designation must be 'Clerk of Works (Electrical)' or 'Superintendent (Electrical)'")
        out["service_no"] = sn
        out["designation"] = body.designation
        out["section"] = EMP_SECTION                     # forced regardless of body
    elif kind == "EMC":
        out["designation"] = EMC_DESIGNATION             # forced; body ignored
        out["company_name"] = actor.company_name         # from token
        out["contractor_number"] = actor.contractor_number  # from token
    else:
        raise err(422, "validation", "Unknown capturer kind")
    return out


def _build_asset(db: Session, actor, body: AssetIn) -> Asset:
    ident = _identity_fields(actor, body)                # validates -> may raise 422
    loc = body.location or LocationIn()
    addr = body.address or AddressIn()
    asset = Asset(
        asset_type_code=body.asset_type_code,
        work_order_id=body.work_order_id,
        installer_id=getattr(actor, "id", None),         # real User -> id; field actor -> None
        contractor_id=getattr(actor, "contractor_id", None),  # from token, never the body
        serial_no=body.serial_no,
        imei=body.imei,
        manufacturer=body.manufacturer,
        model_no=body.model_no,
        manufacture_year=body.manufacture_year,
        wattage=body.wattage,
        controller_manufacturer=body.controller_manufacturer,
        controller_model=body.controller_model,
        pole_no=body.pole_no,
        work_order_no=body.work_order_no,
        region=body.region,
        lat=loc.lat, lng=loc.lng, accuracy_m=loc.accuracy_m,
        road=addr.road, suburb=addr.suburb, city=addr.city,
        town=addr.town, municipality=addr.municipality, province=addr.province,
        notes=body.notes,
        damage_flag=bool(body.damage_flag) if body.damage_flag is not None else False,
        corrosion_flag=bool(body.corrosion_flag) if body.corrosion_flag is not None else False,
        device_id=body.device_id,
        ocr_source=body.ocr_source,
        extracted=body.extracted or {},
        status="pending",
        captured_at=_parse_dt(body.captured_at),
        **ident,                                         # stamped capturer-identity columns
    )
    for p in (body.photos or []):
        asset.photos.append(Photo(kind=p.kind, storage_path=p.storage_path))
    db.add(asset)
    return asset


def _photos_signed(asset: Asset):
    out = []
    for p in asset.photos:
        if p.kind == "identity":      # POPIA: never expose the installer selfie
            continue
        s = signed_get_url(p.storage_path)
        out.append({"kind": p.kind, "url": s["url"], "expires_at": s["expires_at"]})
    return out


def _list_item(asset: Asset):
    return {"id": asset.id, "asset_type": asset.asset_type_code, "serial_no": asset.serial_no,
            "status": asset.status, "suburb": asset.suburb, "captured_at": _iso(asset.captured_at)}


def _detail(asset: Asset):
    return {
        "id": asset.id, "asset_type": asset.asset_type_code, "serial_no": asset.serial_no,
        "imei": asset.imei, "manufacturer": asset.manufacturer,
        "manufacture_year": asset.manufacture_year, "wattage": asset.wattage,
        "pole_no": asset.pole_no, "notes": asset.notes,
        "damage_flag": asset.damage_flag, "corrosion_flag": asset.corrosion_flag,
        "ocr_source": asset.ocr_source,
        "installer": {"id": asset.installer.id, "full_name": asset.installer.full_name} if asset.installer else None,
        "contractor": {"id": asset.contractor.id, "name": asset.contractor.name} if asset.contractor else None,
        "work_order": {"id": asset.work_order.id, "work_order_no": asset.work_order.work_order_no} if asset.work_order else None,
        "location": {"lat": asset.lat, "lng": asset.lng, "accuracy_m": asset.accuracy_m},
        "address": {"road": asset.road, "suburb": asset.suburb,
                    "town": asset.town, "municipality": asset.municipality,
                    "province": asset.province, "city": asset.city},
        "region": asset.region, "model_no": asset.model_no,
        "controller_manufacturer": asset.controller_manufacturer,
        "controller_model": asset.controller_model, "work_order_no": asset.work_order_no,
        "captured_by_kind": asset.captured_by_kind,
        "captured_by_name": asset.captured_by_name, "captured_by_surname": asset.captured_by_surname,
        "service_no": asset.service_no, "designation": asset.designation,
        "company_name": asset.company_name, "contractor_number": asset.contractor_number,
        "captured_at": _iso(asset.captured_at), "created_at": _iso(asset.created_at),
        "status": asset.status, "photos": _photos_signed(asset),
    }


# ------------------------------ endpoints --------------------------------
@router.post("/assets", status_code=201)
def create_asset(body: AssetIn, db: Session = Depends(get_db),
                 actor=Depends(get_current_actor)):
    if _find_duplicate(db, body.serial_no, body.imei):
        raise err(409, "duplicate_unit", "Serial or IMEI already registered")
    asset = _build_asset(db, actor, body)
    db.commit()
    db.refresh(asset)
    return {"id": asset.id, "status": asset.status, "created_at": _iso(asset.created_at)}


@router.get("/assets")
def list_assets(
    status: str | None = None, type: str | None = None,
    contractor_id: str | None = None, work_order_id: str | None = None,
    q: str | None = None, limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None, db: Session = Depends(get_db),
    actor=Depends(get_current_actor),
):
    query = db.query(Asset).filter(Asset.deleted_at.is_(None))
    if not actor_sees_all(actor):
        query = query.filter(Asset.installer_id == actor.id)   # installer sees own only
    if status:
        query = query.filter(Asset.status == status)
    if type:
        query = query.filter(Asset.asset_type_code == type)
    if contractor_id:
        query = query.filter(Asset.contractor_id == contractor_id)
    if work_order_id:
        query = query.filter(Asset.work_order_id == work_order_id)
    if q:
        like = f"%{q}%"
        from sqlalchemy import or_
        query = query.filter(or_(Asset.serial_no.ilike(like), Asset.imei.ilike(like)))

    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            offset = 0
    rows = query.order_by(Asset.created_at.desc()).offset(offset).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = str(offset + limit) if has_more else None
    return {"items": [_list_item(a) for a in rows], "next_cursor": next_cursor}


def _load_owned(db: Session, actor, asset_id: str) -> Asset:
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise err(404, "not_found", "Asset not found")
    if not actor_sees_all(actor) and asset.installer_id != getattr(actor, "id", None):
        raise err(403, "forbidden", "Not your record")
    return asset


def _map_point(a):
    """One GeoJSON Feature for the admin map. Properties are DETAIL-shaped so the
    PWA's mapAsset() adapter consumes them with no change. Same per-row rules as
    the XLSX register (EMP company default, town->city fallback) so the map and
    the export can never show different values for the same luminaire."""
    company = a.company_name or ("eThekwini Municipality"
                                 if a.captured_by_kind == "EMP" else "")
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [a.lng, a.lat]},
        "properties": {
            "id": a.id, "serial_no": a.serial_no,
            "manufacturer": a.manufacturer, "model_no": a.model_no,
            "manufacture_year": a.manufacture_year, "wattage": a.wattage,
            "controller_manufacturer": a.controller_manufacturer,
            "controller_model": a.controller_model, "imei": a.imei,
            "captured_at": _iso(a.captured_at), "work_order_no": a.work_order_no,
            "captured_by_kind": a.captured_by_kind,
            "captured_by_name": a.captured_by_name,
            "captured_by_surname": a.captured_by_surname,
            "designation": a.designation, "service_no": a.service_no,
            "company_name": company, "contractor_number": a.contractor_number,
            "region": a.region,
            "location": {"lat": a.lat, "lng": a.lng},
            "address": {"road": a.road, "suburb": a.suburb,
                        "town": (a.town or a.city), "municipality": a.municipality,
                        "province": a.province},
        },
    }


# IMPORTANT: registered BEFORE /assets/{asset_id} so "geojson" is never captured
# as an {asset_id} path param.
@router.get("/assets/geojson")
def assets_geojson(db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    """Admin-only coordinate feed for the luminaire map. Reuses _register_query()
    (non-deleted streetlight luminaires) so the map's rows are identical to the
    XLSX register. Gated to the single municipal administrator, exactly like the
    /export/* routes (settings.EXPORT_ADMIN_EMPLOYEE_ID, default EMP-0001)."""
    if user.employee_id != settings.EXPORT_ADMIN_EMPLOYEE_ID:
        raise err(403, "forbidden",
                  "Only the municipal administrator may view the map")
    feats = [_map_point(a) for a in _register_query(db)
             if a.lat is not None and a.lng is not None]
    return {"type": "FeatureCollection", "features": feats}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, db: Session = Depends(get_db), actor=Depends(get_current_actor)):
    return _detail(_load_owned(db, actor, asset_id))


class AssetPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str | None = None
    notes: str | None = None


@router.patch("/assets/{asset_id}")
def patch_asset(asset_id: str, body: AssetPatch, db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    asset = _load_owned(db, user, asset_id)
    if body.status is not None:
        asset.status = body.status
    if body.notes is not None:
        asset.notes = body.notes
    db.commit()
    db.refresh(asset)
    return _detail(asset)


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise err(404, "not_found", "Asset not found")
    asset.deleted_at = datetime.now(timezone.utc)   # soft delete (audit trail preserved)
    db.commit()
    return {"id": asset.id, "deleted": True}


@router.delete("/assets/{asset_id}/purge")
def purge_asset(asset_id: str, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    """HARD delete: permanently remove the asset row AND its photo objects from
    storage (R2). Irreversible — unlike DELETE /assets/{id} (soft delete), NO
    audit row survives. Use for POPIA erasure or to clear test data.

    Finds the asset whether or not it was already soft-deleted, so a record that
    was soft-deleted earlier can be purged later during cleanup.

    Order is deliberate: the storage objects are deleted FIRST (idempotently);
    only if that succeeds is the DB row removed. The photo ROWS are removed by
    the ORM cascade (Asset.photos = cascade 'all, delete-orphan') when the asset
    is deleted. If a storage delete fails unexpectedly the row is left intact so
    the call can be retried — this avoids orphaning bytes in the bucket whose
    only pointer (the photo row) has already been deleted.
    """
    asset = db.query(Asset).filter(Asset.id == asset_id).first()  # active OR soft-deleted
    if not asset:
        raise err(404, "not_found", "Asset not found")

    # 1) Purge the bytes in object storage. Collect the keys first; the photo
    #    rows themselves cascade-delete with the asset in step 2.
    paths = [p.storage_path for p in asset.photos if p.storage_path]
    photos_deleted = 0
    for path in paths:
        delete_bytes(path)          # idempotent; raises only on unexpected storage errors
        photos_deleted += 1

    # 2) Hard-delete the row (cascades to the photos table).
    db.delete(asset)
    db.commit()
    return {"id": asset_id, "purged": True, "photos_deleted": photos_deleted}


@router.post("/assets/batch")
def batch(body: BatchIn, db: Session = Depends(get_db), actor=Depends(get_current_actor)):
    results = []
    for item in body.items:
        cu = item.client_uuid
        try:
            # Idempotent replay: same client_uuid already stored -> report created.
            # client_uuid is globally unique; scope by installer only for back-office
            # users (field captures have a NULL installer_id).
            if cu:
                existing_q = db.query(Asset).filter(
                    Asset.client_uuid == cu, Asset.deleted_at.is_(None))
                if not getattr(actor, "is_field", False):
                    existing_q = existing_q.filter(Asset.installer_id == actor.id)
                existing = existing_q.first()
                if existing:
                    results.append({"client_uuid": cu, "id": existing.id, "status": "created"})
                    continue
            if _find_duplicate(db, item.serial_no, item.imei):
                results.append({"client_uuid": cu, "status": "duplicate"})
                continue
            asset = _build_asset(db, actor, item)
            asset.client_uuid = cu
            db.commit()
            db.refresh(asset)
            results.append({"client_uuid": cu, "id": asset.id, "status": "created"})
        except Exception as e:  # one bad item must not sink the batch
            db.rollback()
            msg = getattr(e, "message", None) or str(e) or "validation error"
            results.append({"client_uuid": cu, "status": "error",
                            "error": {"code": getattr(e, "code", "validation"), "message": msg}})
    return {"results": results}


# ---------------------------------------------------------------------------
# Luminaire register export (CSV + XLSX).
# Back-office only (get_current_user) -> reached via /auth/login, NOT the field PWA,
# and further narrowed to the single municipal administrator. Filters to streetlight
# luminaires; one row per non-deleted capture; the 23 agreed columns. Date = capture
# timestamp. CSV and XLSX share ONE query + ONE row builder below, so they cannot
# drift apart.
# ---------------------------------------------------------------------------
def _register_query(db: Session):
    """Every non-deleted streetlight luminaire, oldest capture first."""
    return (db.query(Asset)
              .filter(Asset.deleted_at.is_(None),
                      Asset.asset_type_code == "streetlight_luminaire")
              .order_by(Asset.captured_at)
              .all())


@router.get("/export/luminaires.csv")
def export_luminaires_csv(db: Session = Depends(get_db),
                          user=Depends(get_current_user)):
    # Register download is restricted to the single municipal administrator
    # (settings.EXPORT_ADMIN_EMPLOYEE_ID, default EMP-0001). get_current_user
    # already rejects field tokens (no `sub`); this narrows to that one account.
    # Rows + columns come from app/register.py (shared with the XLSX export).
    if user.employee_id != settings.EXPORT_ADMIN_EMPLOYEE_ID:
        raise err(403, "forbidden",
                  "Only the municipal administrator may download the register")
    csv_text = register.build_register_csv(_register_query(db))
    return StreamingResponse(
        io.StringIO(csv_text),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="nuevo_luminaire_register.csv"'},
    )


@router.get("/export/luminaires.xlsx")
def export_luminaires_xlsx(db: Session = Depends(get_db),
                           user=Depends(get_current_user)):
    # Same admin gate as the CSV. The styled workbook (navy header, typed cells,
    # frozen header, Excel Table "LuminaireRegister") is built by app/register.py
    # from the SAME column spec as the CSV, so the two can never drift.
    if user.employee_id != settings.EXPORT_ADMIN_EMPLOYEE_ID:
        raise err(403, "forbidden",
                  "Only the municipal administrator may download the register")
    xlsx_bytes = register.build_register_xlsx(_register_query(db))
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="nuevo_luminaire_register.xlsx"'},
    )
