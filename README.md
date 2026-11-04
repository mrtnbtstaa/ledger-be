# Receipt Processor API

FastAPI backend for the Ledger demo. Groq extracts receipt fields, deterministic checks verify the arithmetic, and low-confidence or failed receipts go to human review.

## Run locally

Requirements: Python 3.12 or 3.13, [uv](https://docs.astral.sh/uv/), PostgreSQL.

1. Start Postgres on port 5433 (5432 is often already taken):
   - Docker: `docker compose up -d db`
   - Native PostgreSQL: create `.pgdata` once with `initdb -D .pgdata -U postgres -A trust`, then
     `pg_ctl -D .pgdata -o "-p 5433" -l .pgdata/server.log start`
2. Create the databases:
   `createdb -h localhost -p 5433 -U postgres receipts`
   `createdb -h localhost -p 5433 -U postgres receipts_test`
3. `Copy-Item .env.example .env` (PowerShell) and set `GROQ_API_KEY`.
4. `uv sync`
5. `uv run alembic upgrade head`
6. `uv run uvicorn app.main:app --reload`

API docs: <http://127.0.0.1:8000/docs>

## Tests

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy
```

Integration tests use `receipts_test` on port 5433 and disable rate limiting.

## Frontend contract

```powershell
uv run python scripts/export_openapi.py ../receipt_invoice_processorr_fe/contracts/openapi.json
```

Then run `npm run gen:api` in the frontend.

## Notes

- Built-in samples use curated golden results, so they work without LLM quota. Their records and files are pinned and never purged.
- Uploaded files are stored in Postgres and expire after `FILE_RETENTION_HOURS`; `POST /admin/maintenance` (Bearer `ADMIN_TOKEN`) purges expired files and old records.
- `REVIEW_PASSCODE` is empty by default. Set it to require the `X-Review-Passcode` header for review actions.
