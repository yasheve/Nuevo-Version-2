from fastapi import APIRouter, Depends, Query

from ..geocode import reverse_geocode
from ..security import get_current_actor

router = APIRouter()


@router.get("/geocode")
def geocode(lat: float = Query(...), lng: float = Query(...),
            _=Depends(get_current_actor)):
    # Field-reachable: uses get_current_actor (NOT get_current_user), so field
    # tokens carrying cred_id are accepted. Always 200 — empty fields on any
    # failure so the capture form falls back to manual entry (contract §5).
    return reverse_geocode(lat, lng)
