from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import err
from ..models import AccessCredential, User
from ..security import (create_access_token, create_field_token,
                        get_current_user, verify_password)

router = APIRouter()


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _user_obj(user: User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "role": user.role,
        "contractor_id": user.contractor_id,
        "contractor_name": user.contractor.name if user.contractor else None,
    }


class LoginIn(BaseModel):
    employee_id: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.employee_id == body.employee_id, User.active == True).first()  # noqa: E712
    if not user or not verify_password(body.password, user.password_hash):
        raise err(401, "invalid_credentials", "Incorrect employee ID or password")
    token, expires = create_access_token(user)
    return {"token": token, "expires_at": _iso(expires), "user": _user_obj(user)}


class FieldLoginIn(BaseModel):
    kind: str
    password: str


@router.post("/auth/field-login")
def field_login(body: FieldLoginIn, db: Session = Depends(get_db)):
    """Two-type field gate. {kind, password} -> field token. EMC's three passwords
    are disambiguated by which credential hash matches (first match wins; 1 password
    <-> 1 contractor). Validated server-side only — the password is never in the app.
    (added: identity step)
    """
    kind = (body.kind or "").strip().upper()
    if kind not in ("EMP", "EMC"):
        raise err(401, "invalid_credentials", "Incorrect password")  # don't leak which kinds exist
    creds = (db.query(AccessCredential)
               .filter(AccessCredential.kind == kind, AccessCredential.active == True)  # noqa: E712
               .all())
    for cred in creds:
        if verify_password(body.password, cred.password_hash):
            token, expires = create_field_token(cred)
            actor = {"kind": kind}
            if kind == "EMC" and cred.contractor is not None:
                actor["company_name"] = cred.contractor.name
                actor["contractor_number"] = cred.contractor.contractor_number
                actor["contractor_id"] = cred.contractor_id
            return {"token": token, "expires_at": _iso(expires), "actor": actor}
    raise err(401, "invalid_credentials", "Incorrect password")


@router.post("/auth/refresh")
def refresh(user: User = Depends(get_current_user)):
    token, expires = create_access_token(user)
    return {"token": token, "expires_at": _iso(expires), "user": _user_obj(user)}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return _user_obj(user)
