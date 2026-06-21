from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import err
from ..models import AssetType, Contractor, WorkOrder, Asset
from ..security import get_current_actor, require_role

router = APIRouter()


@router.get("/asset-types")
def asset_types(db: Session = Depends(get_db), _=Depends(get_current_actor)):
    rows = db.query(AssetType).order_by(AssetType.name).all()
    return [{"id": r.id, "code": r.code, "name": r.name, "ocr_fields": r.ocr_fields or []} for r in rows]


@router.get("/contractors")
def contractors(db: Session = Depends(get_db), _=Depends(get_current_actor)):
    rows = db.query(Contractor).filter(Contractor.active == True).order_by(Contractor.name).all()  # noqa: E712
    return [{"id": r.id, "name": r.name, "registration_no": r.registration_no} for r in rows]


@router.get("/work-orders")
def work_orders(contractor_id: str | None = None, status: str | None = None,
                q: str | None = None, db: Session = Depends(get_db),
                _=Depends(get_current_actor)):
    query = db.query(WorkOrder)
    if contractor_id:
        query = query.filter(WorkOrder.contractor_id == contractor_id)
    if status:
        query = query.filter(WorkOrder.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(WorkOrder.work_order_no.ilike(like))
    rows = query.order_by(WorkOrder.work_order_no).all()
    return [{"id": r.id, "work_order_no": r.work_order_no, "jobcart_ref": r.jobcart_ref,
             "expected_quantity": r.expected_quantity, "status": r.status} for r in rows]


@router.get("/work-orders/{wo_id}/reconciliation")
def reconciliation(wo_id: str, db: Session = Depends(get_db), _=Depends(require_role("supervisor"))):
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise err(404, "not_found", "Work order not found")
    captured = db.query(Asset).filter(Asset.work_order_id == wo_id, Asset.deleted_at.is_(None)).count()
    expected = wo.expected_quantity or 0
    return {"work_order_no": wo.work_order_no, "expected_quantity": expected,
            "captured_quantity": captured, "variance": expected - captured}
