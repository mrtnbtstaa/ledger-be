# Update Plan: Private Accounts and Per-User Ledger (Backend)

> **This is an update to the existing Ledgerline backend, not a new project.** The baseline plan ([`baseline/backend-plan.md`](../../baseline/backend-plan.md)) still applies except where this document changes it. Only what changes is described here.

**Update Branch**: `003-private-accounts` | **Date**: 2026-11-05 | **Spec**: [spec.md](./spec.md)

---

## Summary

Add JWT authentication to the existing FastAPI service, attach an owner to every receipt, and constrain every query to that owner. Approved receipts become entries in the owner's private ledger. Users can delete receipts and their account. Samples stay public. Existing shared data is purged and the public sheet is replaced.

The approach in five lines:

1. **Small, standard auth**: Argon2id passwords, short-lived HS256 access tokens with a pinned algorithm, opaque rotating refresh tokens stored hashed.
2. **One choke point for isolation**: a repository layer that cannot be called without an owner id, plus an isolation test suite as a required CI check.
3. **Ledger without a new table**: the ledger is the owner's `auto_approved` and `approved` receipts, kept until the user deletes them.
4. **Keep less data**: originals deleted at terminal state, per-user cache, immediate complete deletion.
5. **Ship safely**: stop new uploads first, purge the exposed data, then deploy the new endpoints.

---

## What Changes Relative to the Baseline Plan

| Baseline plan item | Change |
|---|---|
| Decision "cache and de-duplicate by file hash" | Per user: unique on `(user_id, file_sha256)` |
| Decision "file storage in Postgres, 24h expiry" | Files deleted at terminal state. 24h is only a backstop for receipts in review |
| Decision "Google Sheets outbox for approved receipts" | Outbox covers sample rows only. User receipts log to the ledger |
| Decision "optional review passcode" | Removed. Ownership replaces it |
| Decision "rate limiting per IP plus global budget" | Adds per-user daily upload counter and auth endpoint limits |
| Decision "maintenance purges records after 7 days" | Ledger entries are exempt. Other records still expire |
| Samples seeded and pinned | Now ownerless with an explicit `is_sample` flag, readable without a token |
| Data model | Adds `users`, `refresh_tokens`, `user_usage`. Adds `user_id` and `is_sample` to `receipts` |
| Contracts | Adds auth, `/me`, delete, export, ledger filter, `/validate`. Changes access rules on existing endpoints |
| Everything else (pipeline, validation rules, confidence, LLM client, evaluation, health) | **Unchanged** |

---

## Technical Context (additions)

| Item | Decision |
|---|---|
| **Access tokens** | PyJWT, HS256, secret known only to the backend |
| **Passwords** | `argon2-cffi` (or `pwdlib` with Argon2), Argon2id |
| **Export** | stdlib `csv`, `openpyxl` for XLSX |
| **Database** | Same Postgres. Dedicated least-privilege role. Any provider data API disabled |

### Configuration (new environment variables)

| Variable | Purpose |
|---|---|
| `JWT_SECRET` | Signing secret, at least 32 random bytes (secret) |
| `JWT_SECRET_PREVIOUS` | Optional, verification only, used during rotation (secret) |
| `JWT_ISSUER`, `JWT_AUDIENCE` | Defaults `ledgerline-api` and `ledgerline-web` |
| `ACCESS_TOKEN_MINUTES` | Default 15 |
| `REFRESH_TOKEN_DAYS` | Default 14 |
| `REFRESH_REUSE_GRACE_SECONDS` | Default 10 |
| `REGISTRATION_ENABLED`, `MAX_USERS` | Close signups or cap users (default cap 200) |
| `UPLOADS_ENABLED` | Kill switch. When `false`, uploads return a clear message |
| `RATE_LIMIT_AUTH`, `RATE_LIMIT_REGISTER` | Per-IP, for example `10/minute` and `3/hour` |
| `USER_DAILY_UPLOAD_LIMIT` | Default 20 |
| `INACTIVE_ACCOUNT_DAYS` | Optional cleanup of unused accounts (default 180) |

**Removed**: `REVIEW_PASSCODE`.
**Changed meaning**: `FILE_RETENTION_HOURS` is now a backstop for files of receipts still in review. `RECORD_RETENTION_DAYS` applies to non-ledger records only.

---

## Constitution Check (delta)

| Principle | How the update complies |
|---|---|
| **Update, not rewrite** | Pipeline, validation, LLM client, and evaluation are untouched |
| **Private by default** | Owner-required repository layer, 404 on foreign resources, early file deletion, minimal user data |
| **Don't invent crypto** | Argon2id, PyJWT with a pinned algorithm, random opaque refresh tokens |
| **Simplicity** | No cookies or CSRF surface, no new services, no new table for the ledger |
| **Testability** | Isolation and token-attack suites are required checks |

**Result**: Pass.

---

## Key Technical Decisions

### U-D1: Access tokens
- **Format**: HS256 signed with `JWT_SECRET`. Only this backend verifies tokens, so a symmetric secret is appropriate.
- **Claims**: `sub` (user UUID), `iat`, `exp` (now + `ACCESS_TOKEN_MINUTES`), `iss`, `aud`, `jti`. A `kid` header supports rotation.
- **Verification**: `jwt.decode(token, key, algorithms=["HS256"], audience=..., issuer=..., options={"require": ["exp","iat","sub","iss","aud"]})`. The algorithm list is fixed in code and never read from the token. `none` and anything else is rejected.
- **Rotation**: sign with `JWT_SECRET`, verify with it and `JWT_SECRET_PREVIOUS` (selected by `kid`).
- **User lookup**: the `current_user` dependency loads the user by `sub` on every request (one primary-key read) and rejects if missing, so a deleted account loses access immediately.
- **Tradeoff**: an access token cannot be revoked individually and stays valid until `exp`. That is why it is short-lived.
- **Errors**: invalid gives `401 unauthenticated`. Expired gives `401 token_expired`.

### U-D2: Refresh tokens
- **Format**: `secrets.token_urlsafe(32)`, opaque, not a JWT.
- **Storage**: only a SHA-256 hash, in `refresh_tokens` (`id`, `user_id`, `token_hash` unique, `family_id`, `created_at`, `expires_at`, `revoked_at`, `replaced_by`, `last_used_at`).
- **Rotation**: every refresh revokes the presented token and issues a new one in the same family, in one transaction.
- **Reuse detection**: a token that was already rotated and is presented again
  - within `REFRESH_REUSE_GRACE_SECONDS`: treated as a multi-tab race, a new token in the same family is issued, nothing is revoked;
  - otherwise: the whole family is revoked and `401 refresh_invalid` is returned.
- **Logout** revokes the family. **Delete all** and **delete account** remove the user's tokens. Maintenance purges expired and revoked tokens.

### U-D3: Passwords and login protection
- **Hashing**: Argon2id with parameters from the library's current recommendations, recorded in config. Rehash on login when the stored parameters are outdated. Tune so login stays under about a second on the free instance, and test it on the real host.
- **Policy**: 10 to 128 characters, not equal to the email, no composition rules. The length cap is checked before hashing.
- **Enumeration and timing**: for an unknown email, verify against a fixed dummy hash and return the same `invalid_credentials` message.
- **Brute force**: per-IP slowapi limits on auth routes, plus per-account progressive delay (`failed_login_count`, `locked_until` with capped exponential backoff, reset on success). It slows attackers without permanently locking the real owner out.
- **Registration**: normalize the email, validate its format, unique index on the normalized value, `409 email_taken` on conflict (an accepted enumeration tradeoff without email verification), per-IP signup limit, `MAX_USERS` cap, `REGISTRATION_ENABLED` switch.

### U-D4: No cookies
Tokens travel in the `Authorization` header. The backend sets no cookies, so there is no CSRF surface. CORS stays an allowlist of the frontend origin(s).

### U-D5: Owner-scoped repository layer
- All access to receipts, files, audit events, and usage goes through functions in `app/repos/` whose first parameter is `owner_id`, and every query includes `WHERE user_id = :owner_id`.
- A miss and a foreign owner both raise `receipt_not_found` (404).
- A test fails the build if any module outside `app/repos` imports the receipt models for queries.
- **One exception, explicit and small**: `repos.samples.get(sample_id_or_receipt_id)` returns rows where `is_sample = true`. It is the only function that reads ownerless rows.

### U-D6: Receipt read rules
`GET /receipts/{id}`: if the row is a sample, return it without a token. Otherwise require a valid token and ownership. All other receipt endpoints require ownership, and samples are refused for edit, approve, reject, delete, and file access.

### U-D7: Ledger (no new table)
- The ledger is the owner's receipts with status `auto_approved` or `approved`. `GET /receipts?ledger=true` returns exactly those, combinable with category, sort, and pagination.
- Approval is the logging event. The moment the pipeline sets `auto_approved` or the owner approves, the receipt is in the ledger. There is no separate sync step and no external call that can fail.
- Retention: maintenance deletes `failed` and `rejected` records and stale `needs_review` records after `RECORD_RETENTION_DAYS`. It skips `auto_approved` and `approved`. Optional cleanup removes accounts inactive for `INACTIVE_ACCOUNT_DAYS` along with their data.
- Ledger rows keep `data`, `checks`, status, and timestamps. The original file is already gone (U-D8). Audit events stay with the receipt and are deleted with it.

### U-D8: File lifecycle
- At `auto_approved` or `failed`, delete the file row in the same transaction that sets the status.
- `needs_review` keeps the file so the reviewer can compare. Approve and reject delete it. Maintenance deletes any file older than `FILE_RETENTION_HOURS`.
- `fileUrl` is `null` except while a `needs_review` receipt still has its file.

### U-D9: Per-user cache
Replace the global unique index on `file_sha256` with a partial unique index on `(user_id, file_sha256) WHERE user_id IS NOT NULL`. A re-upload by the same user returns the existing receipt with `cached: true`. Concurrent identical uploads by one user are resolved by catching the unique violation. Samples are looked up by `sample_id`.

### U-D10: Deletion and export
- `DELETE /receipts/{id}`: one transaction removes the receipt, file, audit events, and LLM call rows (`ON DELETE CASCADE`).
- `DELETE /receipts`: removes every receipt for the owner and their `user_usage`.
- `DELETE /me` (body: password): verifies the password, deletes the user. Cascading foreign keys remove receipts, files, audit, usage, and refresh tokens.
- In-flight tasks re-check that the receipt exists before each write.
- `GET /receipts/export?format=csv|xlsx&ledger=true|false`: streams the owner's rows. Cells starting with `=`, `+`, `-`, `@`, tab, or carriage return get a leading single quote.

### U-D11: Samples and the public sheet
- Samples are rows with `user_id NULL` and `is_sample = true`, seeded by the existing seeding script.
- `POST /samples/{id}/process` keeps its baseline contract: it returns the sample receipt's id and `cached: true`, with no LLM call, so the frontend's polling flow is unchanged.
- `POST /validate` is new, public, rate limited, and stateless.
- The Sheets integration appends sample rows only, idempotent by sample id. It never reads user receipts. The old sheet is deleted and a new one created (see Rollout).

### U-D12: Database least privilege
- A dedicated role `ledgerline_api` with access limited to the app schema, no superuser, no role creation.
- Migrations use a separate admin credential, used only during deploy.
- If the provider offers a REST or data API, keep it disabled so the backend is the only path to the data.

### U-D13: Logging
The logging filter masks `Authorization` headers, request bodies of auth routes, and any field named like password, token, secret, hash, file content, or extracted text. Logs carry a request id and a short hash of the user id, never the email.

### U-D14: Rate limits
Existing per-IP limits stay. Add `RATE_LIMIT_AUTH` and `RATE_LIMIT_REGISTER` on auth routes, and `USER_DAILY_UPLOAD_LIMIT` checked and incremented in `user_usage` before accepting an upload. The global LLM budget is unchanged.

---

## Project Structure (changes only)

```
backend/app/
├── api/
│   ├── auth.py            # NEW: register, login, refresh, logout
│   ├── me.py              # NEW: GET /me, DELETE /me
│   ├── validate.py        # NEW: POST /validate (stateless, public)
│   ├── export.py          # NEW: GET /receipts/export
│   ├── upload.py          # CHANGED: requires user, per-user limits, UPLOADS_ENABLED
│   ├── receipts.py        # CHANGED: owner-scoped, ledger filter, delete endpoints, sample read rule
│   └── samples.py         # CHANGED: ownerless, no LLM call
├── core/
│   ├── tokens.py          # NEW: access token encode/decode, kid rotation
│   └── passwords.py       # NEW: Argon2id hash, verify, rehash, dummy verify
├── repos/                 # NEW: owner-scoped data access (users, refresh_tokens, receipts, files, audit, usage, samples)
├── services/
│   ├── accounts.py        # NEW: register, login, refresh rotation, logout, delete account
│   ├── deletion.py        # NEW: delete one, all, account
│   ├── export.py          # NEW: CSV/XLSX with formula-injection escaping
│   ├── pipeline.py        # CHANGED: owner on writes, delete file at terminal state, existence re-check
│   ├── retention.py       # CHANGED: ledger exempt, token purge, inactive accounts
│   └── sheets.py          # CHANGED: sample rows only
alembic/versions/          # NEW migrations (see below)
tests/
├── isolation/             # NEW
├── auth/                  # NEW
└── security/              # NEW (token attacks, log redaction, CSV injection)
scripts/
└── generate_secret.py     # NEW
```

Everything not listed is unchanged.

---

## Data Model Changes

### New tables

| Table | Columns | Notes |
|---|---|---|
| `users` | `id` (uuid), `email` (unique, normalized), `password_hash`, `created_at`, `failed_login_count`, `locked_until` | Nothing else about the person |
| `refresh_tokens` | `id`, `user_id` (FK cascade), `token_hash` (unique), `family_id`, `created_at`, `expires_at`, `revoked_at`, `replaced_by`, `last_used_at` | Hashes only |
| `user_usage` | `user_id` (FK cascade), `day`, `uploads` | PK `(user_id, day)` |

### Changed tables

| Table | Change |
|---|---|
| `receipts` | Add `user_id` (FK to `users`, cascade, nullable) and `is_sample` (bool, default false). Replace the unique index on `file_sha256` with a partial unique index on `(user_id, file_sha256) WHERE user_id IS NOT NULL`. Add indexes `(user_id, status, uploaded_at)` and `(user_id, uploaded_at)`. Make `user_id` required unless `is_sample` (check constraint) |
| `receipt_files`, `audit_events`, `llm_calls` | Ensure `ON DELETE CASCADE` from `receipts` |
| `sheet_sync` | Key by `sample_id` |

### Migration order (Alembic)

1. **Data step (destructive, intentional)**: delete all `receipts` where the row is not a sample, with cascading files, audit events, and call records. This is the cleanup in story U6. No backup of the removed rows is kept on purpose.
2. Create `users`, `refresh_tokens`, `user_usage`.
3. Alter `receipts` (columns, indexes, check constraint) and backfill `is_sample = true` for the seeded sample rows.
4. Add cascades. Create the `ledgerline_api` role grants.
5. Truncate `sheet_sync` and re-seed for samples.

Because step 1 cannot be undone, run it only after the uploads kill switch has been on long enough that no new shared data is being written.

---

## Contract Changes

### New endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register` | public, limited | `{email, password}` returns `201 {user, accessToken, refreshToken, expiresIn}` |
| POST | `/auth/login` | public, limited | Same response shape, `200` |
| POST | `/auth/refresh` | public, limited | `{refreshToken}` returns a rotated pair |
| POST | `/auth/logout` | public | `{refreshToken}` returns `204` |
| GET | `/me` | user | `{id, email, createdAt}` |
| DELETE | `/me` | user | `{password}` returns `204` |
| DELETE | `/receipts/{id}` | owner | `204` |
| DELETE | `/receipts` | user | `204` (all the user's receipts) |
| GET | `/receipts/export` | user | `format=csv\|xlsx`, optional `ledger=true` |
| POST | `/validate` | public, limited | `{data}` returns `{checks, confidence}`, stores nothing |

### Changed endpoints

| Endpoint | Before | After |
|---|---|---|
| `POST /upload` | Public | Requires a user token. Returns `503` with a clear code when `UPLOADS_ENABLED=false` |
| `GET /receipts` | All receipts | The user's receipts. Adds `ledger=true` |
| `GET /receipts/{id}` | Any id | Owner only, except samples |
| `GET /receipts/{id}/file` | Any id | Owner only, only while retained, else `404 file_expired` |
| `PATCH`, `approve`, `reject` | Any id (optional passcode) | Owner only, `needs_review` only. `409 invalid_state` otherwise |
| `POST /samples/{id}/process` | Processed the sample | Same contract, returns the stored sample receipt with no LLM call |

### Unchanged endpoints

`GET /health`, `GET /samples`, `GET /eval-summary`, `POST /admin/maintenance` (its response gains `purgedTokens`).

### Receipt response changes

Adds `isSample` (bool). `fileUrl` is `null` unless the receipt is `needs_review` with its file retained. All other fields are unchanged.

### New error codes

| Code | HTTP | Meaning |
|---|---|---|
| `unauthenticated` | 401 | Missing, malformed, or invalid access token |
| `token_expired` | 401 | Access token expired. Refresh and retry |
| `invalid_credentials` | 401 | Wrong email or password (same message for both) |
| `refresh_invalid` | 401 | Refresh token unknown, expired, revoked, or reused. Sign in again |
| `email_taken` | 409 | Email already registered |
| `weak_password` | 422 | Password fails the policy, with the reason |
| `registration_closed` | 403 | Signups disabled or user cap reached |
| `uploads_disabled` | 503 | Kill switch is on |

The `unauthorized` code remains for the admin token. `receipt_not_found` now also covers "owned by someone else".

---

## Rollout and Migration Plan

Because the live demo is exposing data right now, order matters.

1. **Immediately, before any code**: set the uploads kill switch (or, if that switch does not exist yet, remove the public upload route behind a quick flag). Replace the public sheet's sharing with private so the old data stops being viewable.
2. **Build and test** phases 2 to 4 below on a branch, with the isolation and token-attack suites passing.
3. **Deploy backend**: run the migration (step 1 deletes the exposed data), seed samples, create the new sample-only Google Sheet, and **delete the old sheet** rather than clearing it. Sheet version history can keep removed rows visible to editors, and deletion removes that.
4. **Deploy frontend** (companion update) the same day. Until it ships, the old frontend will receive `401` on list and upload calls. That is acceptable and safer than leaving the leak open. Optionally show a maintenance banner.
5. **Keep uploads disabled** until both sides are live, then switch them on.
6. **Verify**: register two accounts, confirm neither sees the other's receipts, delete a receipt, delete all, export, delete an account, and confirm the public sheet contains only samples.
7. **Rotate** any credentials that might have been exposed (admin token, Google service account key if the old sheet was shared widely).
8. **Rollback policy**: never roll back to public listing. If something breaks, turn uploads off and fix forward.

---

## Testing Strategy (additions)

| Level | Covers |
|---|---|
| **Isolation (gate)** | Every endpoint that takes a receipt id or returns a list, as owner, user B, no token, expired token, wrong-key token, and a deleted user's token. Includes cursors, export, file, edit, approve, reject, delete, ledger filter, and identical-file upload by two users |
| **Token attacks (gate)** | Algorithm `none`, wrong secret, wrong issuer or audience, tampered payload, missing `sub`, expired, previous-secret rotation |
| **Accounts** | Register, duplicates, weak passwords, login success and failure, identical error for unknown email and wrong password, per-account delay, signup cap, closed registration |
| **Refresh tokens** | Rotation, old token rejected, grace window race, reuse outside grace revokes the family, logout, expiry |
| **Passwords** | Only Argon2id hashes stored, no plaintext in DB or logs, rehash on parameter change, length cap before hashing |
| **Ledger** | Auto-approved and approved receipts appear in the owner's ledger immediately and in no one else's. Rejected and failed do not. Ledger entries survive maintenance. Non-ledger records expire |
| **Deletion** | Delete one, all, and account remove rows, files, audit, calls, usage, tokens. In-flight task after delete creates nothing. Wrong password cannot delete the account |
| **Samples** | Readable with no token, never listed for users, refused for edit and delete, no LLM call |
| **Migration** | After the data step, zero non-sample receipts remain. Run against a copy of the real schema |
| **Privacy** | File bytes gone at each terminal status. Logs and error bodies free of passwords, tokens, text, and files |
| **Regression** | The existing unit, integration, and evaluation tests still pass unchanged |

The isolation and token-attack suites are required CI checks.

---

## Implementation Phases (input for `/speckit-tasks`)

0. **Stop the leak**: uploads kill switch, make the public sheet private.
1. **Accounts and tokens**: password hashing, register, login, access token issue and verify, refresh rotation with reuse detection, logout, `/me`, auth limits, plus auth and token-attack suites.
2. **Ownership**: new columns and migrations (without the destructive step yet), owner-scoped repositories, `current_user`, convert every receipt endpoint, and the isolation suite.
3. **Ledger, deletion, export**: ledger filter, delete one, all, and account, export with escaping, retention changes.
4. **Samples and `/validate`**: ownerless samples, public read rule, stateless validation.
5. **File lifecycle and quotas**: delete files at terminal state, per-user cache, per-user upload limit.
6. **Sheet change**: sample-only sync, new sheet.
7. **Migration and deploy**: run the destructive migration, deploy, delete the old sheet, rotate credentials, verify, re-enable uploads.

Phases 1 and 2 are the minimum to close the leak. Phase 3 delivers the user-visible value.

---

## Traceability: Spec to Plan

| Spec item | Plan elements |
|---|---|
| U1, FR-U01 to FR-U05 | U-D1, U-D4, U-D5, U-D6, isolation suite |
| U2, FR-U06 to FR-U12 | U-D1 to U-D4, `accounts.py`, `passwords.py`, `tokens.py` |
| U3, FR-U13 to FR-U17 | U-D7, U-D10, `retention.py`, `export.py` |
| U4, FR-U18 to FR-U20 | U-D10, `deletion.py` |
| U5, FR-U25 to FR-U27 | U-D6, U-D11, `samples.py`, `validate.py` |
| U6, FR-U32 to FR-U34 | Migration order, Rollout plan |
| U7, FR-U21 to FR-U24, FR-U29 to FR-U31 | U-D8, U-D9, U-D12, U-D13, U-D14 |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| A query path forgets the owner filter | Cross-user leak | Repository layer requires `owner_id`, import-lint test, isolation suite as a required check |
| Forged or confused JWT accepted | Account takeover | Pinned algorithm, issuer and audience checks, token-attack suite |
| `JWT_SECRET` leaks | Anyone can mint tokens | Env-only, redaction, secret scanning, rotation via `JWT_SECRET_PREVIOUS` |
| Password database leak | Credentials exposed | Argon2id, no plaintext, minimal user data |
| Credential stuffing | Account compromise | Per-IP limits, per-account delay, generic errors, signup cap |
| Refresh token theft | Long-lived access | Hashed storage, rotation, reuse detection, 14-day expiry |
| Old data left behind | Leak persists | Destructive migration, old sheet deleted, kill switch before migration |
| Migration deletes demo data you wanted | Lost samples | Samples are flagged and excluded, verified on a copy first |
| Old frontend breaks after backend deploy | Visible errors | Deploy both the same day, optional maintenance banner |
| Argon2 too slow on the free instance | Slow login | Tune on the real host, test login latency |
| No password reset | Locked-out users | Documented limitation, planned next update |
| Ledger grows unbounded | Free database fills | Small rows, inactive-account cleanup, user cap |
| Groq retains submitted content | Privacy claim inaccurate | Read Groq's current data terms before writing the privacy notice |

---

## Complexity Tracking

| Addition | Why it is justified | Simpler alternative rejected because |
|---|---|---|
| Rotating refresh tokens with reuse detection | Limits the damage of a stolen token | A static or long-lived token stays valid if stolen |
| Per-request user lookup | Deleted accounts lose access immediately | Trusting the token alone leaves a 15-minute window |
| Owner-required repository layer | One reviewable choke point | Per-route filtering is easy to forget |
| Ledger as a filter, not a table | No new moving parts, no sync that can fail | A separate ledger table duplicates data and needs syncing |
| Public precomputed samples and `/validate` | Full demo with no account and no shared mutable data | Requiring sign-up first would hurt the interview demo |
| Symmetric JWT signing | Only this service verifies tokens | Asymmetric keys add key management with no benefit |

---

## Open Items Carried From the Spec

Defaults used until you decide otherwise: ledger kept until deleted with 180-day inactive-account cleanup, 200-user cap, 15-minute access tokens and 14-day rotating refresh tokens, 20 uploads per user per day, and no password reset. Resolve these before generating `tasks.md`.
