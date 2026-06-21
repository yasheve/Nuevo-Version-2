# NuEvo Asset Capture — API (backend)

The server the **NuEvo Capture PWA** talks to. It implements the endpoints in
`nuevo_api_contract.md` (the contract that ships inside the app zip), field for
field. Built so it runs with **zero configuration** for a laptop demo (SQLite +
local file storage), and swaps cleanly to managed Postgres + S3-compatible
storage for production.

> This was rebuilt from the API contract and **verified end to end** (19/19
> checks: login → reference data → presigned upload → PUT bytes → OCR → asset
> create + duplicate guard → list → detail with signed image → trace with
> factory-match → idempotent batch sync). If you already hold an earlier
> `nuevo-api.zip`, either works — the run steps are identical.

## Run it (local, no setup)

```bash
cd nuevo-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- API root: <http://localhost:8000/>
- Interactive docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/v1/health>

Or just `./run.sh`.

On first start it creates `nuevo.db` and seeds demo data.

### Demo accounts (password `nuevo123`)

| Employee ID | Role | Contractor |
|-------------|------|------------|
| `EMP-1043`  | installer | Coastal Electrical CC |
| `EMP-2001`  | supervisor | Coastal Electrical CC |
| `EMP-3001`  | auditor | — |
| `EMP-0001`  | admin | — |

## Endpoints

Everything is under `/v1`. Auth is `Authorization: Bearer <JWT>` on every route
except `/v1/auth/login`, `/v1/health`, and the two signed-URL storage routes.

`auth/login` · `auth/refresh` · `auth/me` · `asset-types` · `contractors` ·
`work-orders` (+ `/{id}/reconciliation`) · `uploads/presign` · `uploads/local`
(PUT) · `uploads` (multipart) · `files` (signed GET) · `ocr` · `assets`
(POST/GET/GET{id}/PATCH/DELETE) · `assets/batch` · `trace` · `audits`
(+ `audits/random`).

## Going to production — three swaps

1. **Database.** Set `DATABASE_URL` to managed Postgres
   (`postgresql+psycopg://…`) and `pip install "psycopg[binary]"`. The ORM is
   Postgres-ready; tables auto-create on start (use Alembic for managed
   migrations once live).
2. **Object storage.** Replace `app/storage.py` with real S3-compatible
   presigning (boto3 → AWS S3 / Cloudflare R2 / Backblaze B2 / MinIO). Keep the
   same function names (`presign_put`, `signed_get_url`) and the request/response
   shapes the app sees do not change. Make the bucket **private**; serve only via
   short-TTL presigned URLs.
3. **OCR.** Set `ANTHROPIC_API_KEY` to turn on live nameplate reading. Without
   it, `/ocr` returns `source:"fallback"` and the installer types fields in —
   the app handles this gracefully.

Also set a long random `JWT_SECRET`, restrict `CORS_ORIGINS` to the app's real
origin, and put the API behind HTTPS.

## Compliance note (not legal advice)

The installer selfie is **biometric data = special personal information under
POPIA**. The app captures but does **not** upload it (`UPLOAD_SELFIE:false`), and
this API never returns `identity`-kind photos. Before enabling that path:
explicit consent, retention policy, and encryption at rest — confirm specifics
with your compliance/legal team.

## Not yet buildable (blocked on external specs)

`integrations/factory/ingest` exists as a stub; the ELIPS and Monday.com
push jobs are designed but cannot be finalised until those systems' real API
specs are available. `factory_match` in `/trace` works against any feed loaded
via the factory table.
