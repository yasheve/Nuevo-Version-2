"""Password hashing (argon2), JWT access tokens, signed file tokens, and the
role/ownership dependencies used by the routers.
"""
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .errors import err
from .models import AccessCredential, User

_ph = PasswordHasher()

ROLES = ["installer", "supervisor", "auditor", "admin"]
_ROLE_RANK = {r: i for i, r in enumerate(ROLES)}


# --- passwords ----------------------------------------------------------
def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# --- access tokens ------------------------------------------------------
def create_access_token(user: User) -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.ACCESS_TTL_HOURS)
    payload = {
        "sub": user.id,
        "typ": "access",
        "role": user.role,
        "employee_id": user.employee_id,
        "contractor_id": user.contractor_id,
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)
    return token, expires


def create_field_token(cred: AccessCredential) -> tuple[str, datetime]:
    """Field (EMP/EMC) access token. No `sub`/User — distinguished by `cred_id`.
    EMC tokens carry contractor identity claims (server-trusted, signed). (added: identity step)
    """
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.ACCESS_TTL_HOURS)
    payload = {
        "typ": "access",
        "kind": cred.kind,
        "cred_id": cred.id,
        "exp": int(expires.timestamp()),
    }
    if cred.kind == "EMC" and cred.contractor is not None:
        payload["contractor_id"] = cred.contractor_id
        payload["company_name"] = cred.contractor.name
        payload["contractor_number"] = cred.contractor.contractor_number
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)
    return token, expires


def _decode(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])


# --- file (storage) signing — separate purpose from access tokens ------
def sign_file_token(storage_path: str, op: str, ttl_minutes: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    payload = {"typ": "file", "op": op, "path": storage_path, "exp": int(expires.timestamp())}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def verify_file_token(token: str, op: str) -> str:
    try:
        data = _decode(token)
    except jwt.ExpiredSignatureError:
        raise err(403, "url_expired", "Signed URL has expired")
    except Exception:
        raise err(403, "bad_signature", "Invalid signed URL")
    if data.get("typ") != "file" or data.get("op") != op:
        raise err(403, "bad_signature", "Token not valid for this operation")
    return data["path"]


# --- current-user dependency -------------------------------------------
def get_current_user(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise err(401, "not_authenticated", "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        data = _decode(token)
    except jwt.ExpiredSignatureError:
        raise err(401, "token_expired", "Token has expired")
    except Exception:
        raise err(401, "invalid_token", "Invalid token")
    if data.get("typ") != "access":
        raise err(401, "invalid_token", "Wrong token type")
    user = db.get(User, data.get("sub"))
    if not user or not user.active:
        raise err(401, "invalid_token", "User not found or inactive")
    return user


def require_role(min_role: str):
    """Dependency factory enforcing a minimum role."""
    min_rank = _ROLE_RANK[min_role]

    def _dep(user: User = Depends(get_current_user)) -> User:
        if _ROLE_RANK.get(user.role, -1) < min_rank:
            raise err(403, "forbidden", f"Requires role {min_role} or higher")
        return user

    return _dep


def can_see_all(user: User) -> bool:
    """supervisor+ may see every record; installer sees only their own."""
    return _ROLE_RANK.get(user.role, 0) >= _ROLE_RANK["supervisor"]


# --- field/back-office actor abstraction (added: identity step) ----------
class Actor:
    """Uniform caller identity for capture/read endpoints. Wraps EITHER a real
    back-office `User` OR a field credential. Exposes the small surface the
    routers read (`id`, `role`, `contractor_id`, ...) so existing call-sites work
    unchanged for users. For field actors `id` (installer_id) is None.
    """
    def __init__(self, *, user: User = None, kind: str = None, cred_id: str = None,
                 contractor_id: str = None, company_name: str = None,
                 contractor_number: str = None):
        self.user = user
        self.is_field = user is None
        self.kind = kind                                      # 'EMP'|'EMC' for field; None for back-office user
        self.cred_id = cred_id
        self.id = user.id if user else None                   # == installer_id; None for field
        self.role = user.role if user else None
        self.full_name = user.full_name if user else None
        self.employee_id = user.employee_id if user else None
        self.contractor_id = user.contractor_id if user else contractor_id
        self.company_name = company_name
        self.contractor_number = contractor_number


def get_current_actor(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
) -> Actor:
    """Accept a field token (no User row) OR a back-office user token. Field
    tokens are re-validated against the live AccessCredential (so deactivation /
    rotation takes effect immediately) and EMC identity is re-derived from the
    credential's contractor — authoritative, never the request body.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise err(401, "not_authenticated", "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        data = _decode(token)
    except jwt.ExpiredSignatureError:
        raise err(401, "token_expired", "Token has expired")
    except Exception:
        raise err(401, "invalid_token", "Invalid token")
    if data.get("typ") != "access":
        raise err(401, "invalid_token", "Wrong token type")

    cred_id = data.get("cred_id")
    if cred_id:                                  # field token
        cred = db.get(AccessCredential, cred_id)
        if not cred or not cred.active:
            raise err(401, "invalid_token", "Credential not found or inactive")
        company_name = contractor_number = contractor_id = None
        if cred.kind == "EMC" and cred.contractor is not None:
            contractor_id = cred.contractor_id
            company_name = cred.contractor.name
            contractor_number = cred.contractor.contractor_number
        return Actor(kind=cred.kind, cred_id=cred.id, contractor_id=contractor_id,
                     company_name=company_name, contractor_number=contractor_number)

    user = db.get(User, data.get("sub"))         # back-office token
    if not user or not user.active:
        raise err(401, "invalid_token", "User not found or inactive")
    return Actor(user=user)


def actor_sees_all(actor) -> bool:
    """Field actors have no per-person installer_id to scope by, so they see the
    shared record pool. Back-office actors keep the supervisor+ rule. Tolerant of
    a raw `User` (so back-office-only endpoints can pass one).
    """
    if getattr(actor, "is_field", False):
        return True
    user = getattr(actor, "user", None) or actor
    return can_see_all(user)
