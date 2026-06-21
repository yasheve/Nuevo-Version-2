from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import err
from ..models import Asset, FactoryUnit
from ..security import actor_sees_all, get_current_actor
from ..storage import signed_get_url
from .assets import _iso

router = APIRouter()


@router.get("/trace")
def trace(serial: str | None = None, imei: str | None = None,
          db: Session = Depends(get_db), actor=Depends(get_current_actor)):
    if not serial and not imei:
        raise err(400, "validation", "Provide serial or imei")

    q = db.query(Asset).filter(Asset.deleted_at.is_(None))
    if serial:
        q = q.filter(Asset.serial_no == serial)
    if imei:
        q = q.filter(Asset.imei == imei)
    asset = q.order_by(Asset.created_at.desc()).first()
    if not asset:
        raise err(404, "not_found", "No asset matches that serial / IMEI")

    # installer may trace only their own record; supervisor+ / field actors any.
    if not actor_sees_all(actor) and asset.installer_id != getattr(actor, "id", None):
        raise err(403, "forbidden", "Not your record")

    # factory_match: true | false | null (null = no factory feed loaded / not found)
    factory_match = None
    if db.query(FactoryUnit).count() > 0:
        fu = db.query(FactoryUnit)
        if asset.serial_no:
            fu = fu.filter(FactoryUnit.serial_no == asset.serial_no)
        elif asset.imei:
            fu = fu.filter(FactoryUnit.imei == asset.imei)
        factory_match = fu.first() is not None

    photos = []
    for p in asset.photos:
        if p.kind == "identity":
            continue
        s = signed_get_url(p.storage_path)
        photos.append({"kind": p.kind, "url": s["url"], "expires_at": s["expires_at"]})

    return {
        "asset": {"id": asset.id, "serial_no": asset.serial_no, "imei": asset.imei, "status": asset.status},
        "installer": {"id": asset.installer.id, "full_name": asset.installer.full_name,
                      "employee_id": asset.installer.employee_id} if asset.installer else None,
        "contractor": {"id": asset.contractor.id, "name": asset.contractor.name} if asset.contractor else None,
        "work_order": {"id": asset.work_order.id, "work_order_no": asset.work_order.work_order_no,
                       "jobcart_ref": asset.work_order.jobcart_ref} if asset.work_order else None,
        "location": {"lat": asset.lat, "lng": asset.lng, "suburb": asset.suburb},
        "captured_at": _iso(asset.captured_at),
        "photos": photos,
        "factory_match": factory_match,
    }
