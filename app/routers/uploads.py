from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel

from ..errors import err
from ..security import require_role, get_current_actor, verify_file_token
from ..storage import presign_put, write_bytes, read_bytes

router = APIRouter()

_ALLOWED_KINDS = {"plate", "overall", "identity"}


class PresignIn(BaseModel):
    kind: str
    content_type: str


@router.post("/uploads/presign")
def presign(body: PresignIn, _=Depends(get_current_actor)):
    if body.kind not in _ALLOWED_KINDS:
        raise err(400, "validation", f"kind must be one of {sorted(_ALLOWED_KINDS)}")
    return presign_put(body.kind, body.content_type)


# Bytes land here via PUT — NO bearer auth: the signature is the token in the URL.
@router.put("/uploads/local")
async def upload_local(token: str, request: Request):
    storage_path = verify_file_token(token, op="put")
    data = await request.body()
    if not data:
        raise err(400, "empty_body", "No bytes received")
    write_bytes(storage_path, data)
    return {"storage_path": storage_path, "bytes": len(data)}


# Multipart fallback for clients that can't PUT to storage directly.
@router.post("/uploads")
async def upload_multipart(kind: str = "plate", file: UploadFile = File(...),
                           _=Depends(get_current_actor)):
    if kind not in _ALLOWED_KINDS:
        raise err(400, "validation", f"kind must be one of {sorted(_ALLOWED_KINDS)}")
    presigned = presign_put(kind, file.content_type or "application/octet-stream")
    write_bytes(presigned["storage_path"], await file.read())
    return {"storage_path": presigned["storage_path"]}


# Signed image read — NO bearer auth: token in the URL grants short-lived access.
@router.get("/files")
def get_file(token: str):
    storage_path = verify_file_token(token, op="get")
    data = read_bytes(storage_path)
    if data is None:
        raise err(404, "not_found", "Object not found")
    media = "image/png" if storage_path.endswith(".png") else "image/jpeg"
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "private, max-age=300"})
