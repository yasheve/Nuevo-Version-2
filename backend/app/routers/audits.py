import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import err
from ..models import Asset, Audit
from ..security import require_role

router = APIRouter()


class RandomIn(BaseModel):
    sample_size: int = 10
    days_gap: int = 30


@router.post("/audits/random")
def random_audit(body: RandomIn, db: Session = Depends(get_db), _=Depends(require_role("auditor"))):
    ids = [a.id for a in db.query(Asset.id).filter(Asset.deleted_at.is_(None)).all()]
    pick = random.sample(ids, min(body.sample_size, len(ids))) if ids else []
    created = []
    for aid in pick:
        au = Audit(asset_id=aid, status="open")
        db.add(au)
        db.flush()
        created.append({"id": au.id, "asset_id": aid, "status": "open"})
    db.commit()
    return {"audits": created}


@router.get("/audits")
def list_audits(status: str | None = None, db: Session = Depends(get_db),
                _=Depends(require_role("auditor"))):
    q = db.query(Audit)
    if status:
        q = q.filter(Audit.status == status)
    return [{"id": a.id, "asset_id": a.asset_id, "status": a.status,
             "findings": a.findings} for a in q.order_by(Audit.created_at.desc()).all()]


class AuditPatch(BaseModel):
    status: str | None = None
    findings: str | None = None
    audited_at: str | None = None


@router.patch("/audits/{audit_id}")
def patch_audit(audit_id: str, body: AuditPatch, db: Session = Depends(get_db),
                _=Depends(require_role("auditor"))):
    au = db.get(Audit, audit_id)
    if not au:
        raise err(404, "not_found", "Audit not found")
    if body.status is not None:
        au.status = body.status
    if body.findings is not None:
        au.findings = body.findings
    if body.audited_at:
        try:
            au.audited_at = datetime.fromisoformat(body.audited_at.replace("Z", "+00:00"))
        except Exception:
            au.audited_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": au.id, "status": au.status, "findings": au.findings}
