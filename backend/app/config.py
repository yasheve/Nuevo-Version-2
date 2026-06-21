"""Environment-driven settings for the NuEvo Asset Capture API.

Every value has a safe local-dev default so the server runs with ZERO config.
For production, override via real environment variables (see .env.example).
"""
import os


def _bool(v: str, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- database -------------------------------------------------------
    # Local dev default = SQLite file. Production = managed Postgres, e.g.
    #   postgresql+psycopg://user:pass@host:5432/nuevo
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./nuevo.db")

    # --- auth / JWT -----------------------------------------------------
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-only-change-me-in-production")
    JWT_ALG: str = "HS256"
    ACCESS_TTL_HOURS: int = int(os.getenv("ACCESS_TTL_HOURS", "12"))

    # --- object storage (local shim; swap for S3/R2/B2/MinIO in prod) ---
    STORAGE_DIR: str = os.getenv("STORAGE_DIR", "./storage")
    # The URL the *browser/phone* uses to reach this API for presigned
    # PUT and signed image GET. Must be publicly reachable in production.
    PUBLIC_BASE: str = os.getenv("PUBLIC_BASE", "http://localhost:8000")
    PRESIGN_PUT_TTL_MIN: int = int(os.getenv("PRESIGN_PUT_TTL_MIN", "10"))
    SIGNED_GET_TTL_MIN: int = int(os.getenv("SIGNED_GET_TTL_MIN", "15"))

    # --- CORS -----------------------------------------------------------
    # Comma-separated list of allowed origins for the PWA. "*" for dev only.
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

    # --- OCR (server-side; model key NEVER leaves the server) -----------
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OCR_MODEL: str = os.getenv("OCR_MODEL", "claude-sonnet-4-6")

    # --- seeding --------------------------------------------------------
    SEED_ON_START: bool = _bool(os.getenv("SEED_ON_START"), True)
    SEED_DEMO_PASSWORD: str = os.getenv("SEED_DEMO_PASSWORD", "nuevo123")

    @property
    def cors_list(self):
        v = self.CORS_ORIGINS.strip()
        if v == "*":
            return ["*"]
        return [o.strip() for o in v.split(",") if o.strip()]


settings = Settings()
