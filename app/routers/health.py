from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
