"""NuEvo Asset Capture API — entrypoint.

Run:  uvicorn app.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import Base, SessionLocal, engine
from .errors import APIError, api_error_handler
from .routers import assets, audits, auth, geocode, health, ocr, reference, trace, uploads
from .seed import seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.STORAGE_DIR, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    if settings.SEED_ON_START:
        db = SessionLocal()
        try:
            seed(db)
        finally:
            db.close()
    yield


app = FastAPI(title="NuEvo Asset Capture API", version="1.0.0", lifespan=lifespan)

# CORS — the PWA runs on a different origin and must be allowed to call the API,
# PUT presigned uploads, and GET signed images.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["*"],
)

app.add_exception_handler(APIError, api_error_handler)

# Everything is versioned under /v1 (the app's CONFIG.API_BASE includes /v1).
V1 = "/v1"
app.include_router(health.router, prefix=V1, tags=["health"])
app.include_router(auth.router, prefix=V1, tags=["auth"])
app.include_router(reference.router, prefix=V1, tags=["reference"])
app.include_router(uploads.router, prefix=V1, tags=["uploads"])
app.include_router(ocr.router, prefix=V1, tags=["ocr"])
app.include_router(geocode.router, prefix=V1, tags=["geocode"])
app.include_router(assets.router, prefix=V1, tags=["assets"])
app.include_router(trace.router, prefix=V1, tags=["trace"])
app.include_router(audits.router, prefix=V1, tags=["audits"])


@app.get("/")
def root():
    return {"service": "NuEvo Asset Capture API", "docs": "/docs", "health": f"{V1}/health"}
