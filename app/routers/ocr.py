from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AssetType
from ..ocr import run_ocr
from ..security import require_role

router = APIRouter()


class OCRIn(BaseModel):
    asset_type_code: str
    storage_path: str | None = None
    image_base64: str | None = None


@router.post("/ocr")
def ocr(body: OCRIn, db: Session = Depends(get_db), _=Depends(require_role("installer"))):
    at = db.query(AssetType).filter(AssetType.code == body.asset_type_code).first()
    ocr_fields = (at.ocr_fields if at else None) or []
    # Always 200, even on failure (contract §5).
    return run_ocr(body.asset_type_code, ocr_fields, body.storage_path, body.image_base64)
