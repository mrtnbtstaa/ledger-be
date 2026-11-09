# Update Specification: Private Accounts and Per-User Ledger (Backend)

> **This is an update to the existing Ledgerline project, not a new project.** Everything in the baseline backend spec ([`baseline/backend-spec.md`](../../baseline/backend-spec.md)) and plan ([`baseline/backend-plan.md`](../../baseline/backend-plan.md)) still applies unless this document says otherwise. Read this file as a set of changes on top of the baseline.

**Update Branch**: `003-private-accounts`
**Created**: 2026-11-05
**Status**: Draft
**Applies to**: FastAPI backend, Postgres schema, evaluation harness (unchanged), deployment configuration
**Companion docs**: `update-private-accounts/backend/plan.md`, `update-private-accounts/frontend/spec.md`, `update-private-accounts/frontend/plan.md`

---

## Update Summary

The live Ledgerline treats every visitor the same: all receipts are shared, so anyone can list another visitor's receipts, open their original file, and see their rows in the public Google Sheet. If one visitor accidentally uploads something sensitive, everyone can see it.

This update adds **JWT-based accounts** so that:

1. Each visitor only ever sees their own receipts.
2. Each user can delete their own receipts, and their account, at any time.
3. Each user's approved receipts are **logged automatically to their own account ledger**, a private, persistent list that replaces the shared public sheet for real data.

Built-in samples stay public and need no account.

---

## The Problem in the Current Ledgerline

| Where it leaks | Cause in the current system | What this update does |
|---|---|---|
| History and review queue | The list endpoint returns every visitor's receipts | Lists return only the signed-in user's receipts |
| The original file | `GET /receipts/{id}/file` serves any id, and the list hands out ids | File access requires ownership, and originals are deleted when no longer needed |
| The public Google Sheet | Every approved receipt becomes a public row | User receipts are never written to a shared sheet. The sheet shows samples only |
| The hash cache | The same file uploaded by two people returns the first person's result | The cache is per user. Only samples are shared |
| Review actions | Anyone can edit, approve, or reject any receipt (the optional passcode is the only guard) | Only the owner can change a receipt |
| Retention | Everything is kept for the same short window, with no way for a user to remove their own data | Users can delete immediately, and originals are removed as soon as processing ends |

---

## Scope of This Update

**Changes**

- Registration, login, token refresh, logout, current user, and account deletion
- Ownership on every receipt, file, and audit event, with owner-scoped access on every user endpoint
- A per-user ledger: approved receipts are recorded automatically in the owner's account and kept until the user deletes them
- Delete one, delete all, and delete account; export of the user's ledger
- Per-user cache, quotas, and file lifecycle
- Sample receipts made explicitly public, ownerless, and read-only, plus a stateless validation endpoint
- A one-time cleanup of existing shared data and a replacement of the public sheet

**Unchanged (inherited from the baseline)**

- Extraction pipeline (text layer, vision model, schema-constrained output, one repair)
- Deterministic validation rules, tolerances, computed confidence, and routing
- LLM client behavior (retries, fallback model, global daily budget)
- Background processing, stuck-receipt recovery, health endpoint, error envelope, request ids
- Evaluation harness and `/eval-summary`
- Docker, Render hosting, CI, and maintenance workflow structure

---

## Baseline Items Affected

| Baseline item | Change | Detail |
|---|---|---|
| FR-004 (hash de-duplication) | **Modified** | De-duplication is per user, never across users |
| FR-005 (immediate `processing` response) | Unchanged | Now also requires a valid token |
| FR-020, FR-021 (edit, approve, reject) | **Modified** | Owner only. Approved and rejected receipts lose their original file |
| FR-023 (optional review passcode) | **Removed** | Ownership replaces it |
| FR-024, FR-025, FR-026 (detail, list, file) | **Modified** | Owner-scoped. Samples remain readable without an account |
| FR-027 (sample endpoints) | **Modified** | Samples are ownerless and read-only. `POST /samples/{id}/process` keeps its contract |
| FR-029, FR-030 (Google Sheet logging) | **Replaced** | User receipts log to the user's own ledger. The public sheet holds sample rows only |
| FR-033 (rate limits) | **Extended** | Adds per-user daily upload limits and auth endpoint limits |
| FR-034 (stored samples when LLM unavailable) | Unchanged | Samples now never call the LLM at request time |
| FR-036 (retention) | **Modified** | Originals deleted at terminal state. Ledger entries kept until the user deletes them. Other records expire |
| FR-038 (secrets in env) | **Extended** | Adds the JWT signing secret and a rule against logging passwords and tokens |
| Baseline assumption "no user accounts" | **Removed** | |
| Baseline out of scope "user accounts, authentication" | **Removed** | |
| Baseline Open Question on passcode and session scoping | **Resolved** | Ownership via accounts |

---

## User Scenarios & Testing *(mandatory)*

### User Story U1 - Keep each user's receipts private (Priority: P1)

A signed-in user can only see and change their own receipts. Another user, or an anonymous caller, gets nothing, not even confirmation that a receipt exists.

**Why this priority**: This is the reason for the update.

**Independent Test**: Create a receipt as user A. As user B, and with no token, call every endpoint that takes a receipt id and every list endpoint. Nothing of A's is returned or confirmed.

**Acceptance Scenarios**:

1. **Given** user A owns a receipt, **When** user B requests it by id, **Then** the API returns `404 receipt_not_found`, identical to a nonexistent id.
2. **Given** user A owns a receipt, **When** user B requests its file, edits, approves, rejects, or deletes it, **Then** each returns the same `404` and nothing changes.
3. **Given** users A and B each have receipts, **When** B lists receipts, **Then** only B's appear, including in counts and pagination cursors.
4. **Given** no token, an expired token, a token signed with a different key, or a malformed token, **When** any user-data endpoint is called, **Then** the API returns `401 unauthenticated` and no data.
5. **Given** both users upload the byte-identical file, **When** B uploads it, **Then** B's receipt is processed independently and never reveals A's result.
6. **Given** a valid token for a user whose account was deleted, **When** any user-data endpoint is called, **Then** it returns `401 unauthenticated`.

---

### User Story U2 - Create an account and sign in with JWT (Priority: P1)

A visitor creates an account with an email and password, signs in, stays signed in through short-lived tokens that renew automatically, and signs out.

**Why this priority**: Accounts are what make privacy and a personal ledger possible.

**Independent Test**: Register, sign in, call a protected endpoint, refresh the session, sign out, and confirm the refresh token no longer works.

**Acceptance Scenarios**:

1. **Given** a valid email and a password of acceptable strength, **When** the visitor registers, **Then** an account is created and they receive an access token and a refresh token.
2. **Given** an email already registered, **When** someone registers with it, **Then** the API returns `409 email_taken` and creates nothing.
3. **Given** a weak password (too short, too long, or equal to the email), **When** registering, **Then** the API returns `422 weak_password` with the reason.
4. **Given** correct credentials, **When** the user signs in, **Then** they receive a short-lived access token and a longer-lived refresh token.
5. **Given** an unknown email or a wrong password, **When** the user signs in, **Then** the response, message, and timing are indistinguishable (`401 invalid_credentials`).
6. **Given** an expired access token and a valid refresh token, **When** the client refreshes, **Then** it receives a new access token and a **new** refresh token, and the old refresh token stops working.
7. **Given** a rotated refresh token is presented again after a short grace window, **When** it is used, **Then** the whole token family is revoked and the API returns `401 refresh_invalid`.
8. **Given** a signed-in user, **When** they sign out, **Then** the refresh token family is revoked.
9. **Given** repeated failed sign-ins, **When** more attempts arrive, **Then** they are slowed or limited per IP and per account.
10. **Given** registration is closed or the user cap is reached, **When** someone registers, **Then** the API returns `403 registration_closed`.

---

### User Story U3 - Automatically log my receipts to my own ledger (Priority: P1)

When a user's receipt is auto-approved, or the user approves it after review, it is recorded in that user's own ledger automatically. The ledger is private, persistent, and exportable, and it replaces the shared public sheet for real data.

**Why this priority**: This is the user-facing value of having an account, and it removes the need to write anyone's data to a shared sheet.

**Independent Test**: Upload a clean receipt as a user, wait for `auto_approved`, and confirm it appears in that user's ledger list without further action, and in no other user's.

**Acceptance Scenarios**:

1. **Given** a receipt reaches `auto_approved`, **When** processing finishes, **Then** it is part of the owner's ledger immediately, with no extra step.
2. **Given** a `needs_review` receipt, **When** the owner approves it, **Then** it joins the owner's ledger immediately.
3. **Given** a receipt is `rejected` or `failed`, **When** the ledger is listed, **Then** it does not appear.
4. **Given** a user lists their ledger, **When** they request it with the ledger filter, **Then** only their `auto_approved` and `approved` receipts are returned.
5. **Given** a ledger entry, **When** its data is read, **Then** it contains the extracted fields, checks, status, and timestamps, and no original file.
6. **Given** a ledger entry older than the normal record retention period, **When** maintenance runs, **Then** it is **not** purged. It remains until the user deletes it or deletes their account.
7. **Given** a user exports their ledger, **When** they pick CSV or XLSX, **Then** they receive their ledger rows only, with formula-injection protection.
8. **Given** a user receipt is approved, **When** the sample-sheet sync runs, **Then** nothing about that receipt is written to any shared sheet.

---

### User Story U4 - Delete my own receipts and my account (Priority: P1)

A user can delete a single receipt, delete all receipts, or delete their account, and the deletion is immediate and complete.

**Why this priority**: People upload sensitive documents by mistake and must be able to undo it.

**Independent Test**: Create receipts, delete one, then all, then the account, and confirm no rows, file bytes, audit entries, or tokens remain.

**Acceptance Scenarios**:

1. **Given** a user's receipt (including a ledger entry), **When** the user deletes it, **Then** the record, file bytes, audit events, and LLM call records tied to it are removed immediately.
2. **Given** a user with many receipts, **When** the user deletes all, **Then** every receipt, file, audit event, and usage record for that user is removed.
3. **Given** a user requests account deletion with the correct password, **When** it succeeds, **Then** all receipts, usage, refresh tokens, and the account are removed.
4. **Given** account deletion with a wrong password, **When** it is attempted, **Then** it returns `401 invalid_credentials` and deletes nothing.
5. **Given** another user's receipt id, **When** a deletion is attempted, **Then** it returns `404` and deletes nothing.
6. **Given** a receipt is being processed, **When** the user deletes it, **Then** the in-flight task discards its result and recreates nothing.

---

### User Story U5 - Samples still work without an account (Priority: P1)

Anyone can load a built-in sample and see a complete result, including one that fails validation, without signing in. This preserves the baseline behavior.

**Why this priority**: The interviewer's first click must still work, with no sign-up.

**Independent Test**: With no token and the LLM key removed, process each sample and read its result.

**Acceptance Scenarios**:

1. **Given** no token, **When** the sample list is requested, **Then** at least three samples are listed, including one that fails validation.
2. **Given** no token, **When** `POST /samples/{id}/process` is called, **Then** it returns the id of the stored sample receipt with `cached: true` and makes no LLM call.
3. **Given** a sample receipt id, **When** `GET /receipts/{id}` is called with no token, **Then** the stored result is returned. For any non-sample receipt, a token and ownership are required.
4. **Given** no token, **When** edited sample data is submitted to the stateless validation endpoint, **Then** fresh checks and confidence are returned and nothing is stored.
5. **Given** a user lists receipts, **When** results return, **Then** samples never appear among them.
6. **Given** a visitor tries to edit, approve, reject, or delete a sample, **When** the request is made, **Then** it is refused.

---

### User Story U6 - Clean up the data that is already exposed (Priority: P1)

Before the new endpoints go live, receipts uploaded under the old shared model are removed, since they have no owner and may be sensitive.

**Why this priority**: The old data is the data that leaked. Leaving it would keep the leak alive.

**Independent Test**: After migration, no non-sample receipt, file, or audit row exists without an owner, and the old public sheet is gone.

**Acceptance Scenarios**:

1. **Given** the existing database, **When** the migration runs, **Then** all non-sample receipts, their files, audit events, and related call records are deleted.
2. **Given** the migration finished, **When** the receipts table is inspected, **Then** only sample receipts have no owner.
3. **Given** the old public Google Sheet, **When** the update is deployed, **Then** it is deleted (not just emptied) and replaced by a new sample-only sheet, since sheet version history can keep removed rows.
4. **Given** the update is not yet deployed, **When** the uploads kill switch is on, **Then** uploads are refused with a clear message and no new shared data is created.

---

### User Story U7 - Keep as little data as possible (Priority: P2)

Data is minimized in what is stored and how long it is kept.

**Independent Test**: Process a receipt to a terminal state and confirm its file bytes are gone. Advance time past the retention window and confirm non-ledger records are purged while ledger entries stay.

**Acceptance Scenarios**:

1. **Given** a receipt reaches `auto_approved`, `approved`, `rejected`, or `failed`, **When** processing or review finishes, **Then** its original file bytes are deleted.
2. **Given** a receipt in `needs_review`, **When** the file backstop elapses, **Then** the bytes are deleted even if review never happens.
3. **Given** a `failed` or `rejected` record older than the record retention period, **When** maintenance runs, **Then** it is deleted.
4. **Given** user data, **When** it is stored, **Then** only an email, a password hash, and a creation time are kept about the person.
5. **Given** expired or revoked refresh tokens, **When** maintenance runs, **Then** they are deleted.
6. **Given** any request, **When** it is logged, **Then** the log carries a request id and never a password, token, file, or extracted text.

---

### Edge Cases (new or changed)

- **Access token expires mid-upload or mid-poll**: `401 token_expired`. The client refreshes and retries. In-flight processing continues server-side.
- **Token with algorithm `none`, an unexpected algorithm, a wrong issuer or audience, a tampered payload, or no subject**: Rejected.
- **Token signed with the previous secret during rotation**: Accepted until the previous secret is removed, then rejected.
- **Two tabs refresh at once**: A second use of the same refresh token inside the grace window gets a fresh token without revoking the family. Outside the window it is treated as theft.
- **Email differing only by case or spaces**: Normalized, so it cannot create a duplicate account.
- **Very long password or unusual Unicode**: Length-capped before hashing.
- **Many failed logins for one email from many IPs**: Per-account slowdown applies without permanently locking the owner out.
- **Mass registration**: Per-IP signup limits, a user cap, and a switch to close registration.
- **Same file uploaded by two users concurrently**: Two independent receipts.
- **Delete during processing**: The in-flight task discards its result.
- **Export of values starting with `=`, `+`, `-`, or `@`**: Escaped.
- **Forgotten password**: No recovery in this update (see Out of Scope).
- **Database unavailable**: Health reports it and user requests fail with a generic `503`.

---

## Requirements *(mandatory)*

IDs use the prefix `FR-U` so they do not collide with the baseline's `FR-` numbers.

### Functional Requirements

**Identity and access**

- **FR-U01**: Every endpoint that reads or writes user data MUST require a valid access token. Public endpoints are: health, sample list, sample processing, sample receipt reads, the stateless validation endpoint, the evaluation summary, and register, login, refresh, and logout.
- **FR-U02**: The system MUST verify each access token's signature with the server's secret, accept only a pinned algorithm, and check issuer, audience, and expiry, requiring a subject claim. It MUST support secret rotation by verifying with a previous secret while signing with the current one.
- **FR-U03**: After verifying a token, the system MUST load the user named by its subject on every request and reject the request if the user no longer exists.
- **FR-U04**: Every query touching receipts, files, audit events, usage, or exports MUST be constrained to the authenticated owner. Access to a resource owned by someone else MUST return the same `404` as a nonexistent resource.
- **FR-U05**: `GET /receipts/{id}` MUST be readable without a token only when the receipt is a sample. Otherwise it MUST require ownership.

**Accounts and sessions**

- **FR-U06**: The system MUST allow registration with an email and password. Emails MUST be normalized (trimmed, lowercased) and unique. Passwords MUST be 10 to 128 characters and MUST NOT equal the email. Registration MUST be closable and capped by a total user limit.
- **FR-U07**: Passwords MUST be stored only as a slow, salted hash (Argon2id). Plaintext MUST never be stored. Hashes MUST be upgraded when parameters change.
- **FR-U08**: Login MUST return a short-lived access token and a longer-lived refresh token. Unknown email and wrong password MUST produce identical responses with roughly equal timing.
- **FR-U09**: The system MUST limit registration and login attempts per IP and slow repeated failures per account without permanently locking an account.
- **FR-U10**: Refresh tokens MUST be opaque random values, stored only as hashes, expire, and rotate on every use. Reuse of a rotated token outside a short grace window MUST revoke the whole token family.
- **FR-U11**: Logout MUST revoke the presented token's family. Account deletion MUST revoke all of the user's refresh tokens.
- **FR-U12**: A current-user endpoint MUST return only the user's id and email.

**Ledger**

- **FR-U13**: When a receipt becomes `auto_approved` or `approved`, it MUST be part of the owner's ledger immediately and automatically.
- **FR-U14**: A ledger entry MUST consist of extracted data, validation checks, status, and timestamps, and MUST NOT retain the original file.
- **FR-U15**: Ledger entries MUST be kept until the owner deletes them or deletes their account. All other records MUST expire after the record retention period.
- **FR-U16**: The list endpoint MUST support a ledger filter that returns only the owner's `auto_approved` and `approved` receipts.
- **FR-U17**: The API MUST let a user export their ledger (or all their receipts) as CSV or XLSX, escaping cell values that could execute as formulas.

**Deletion**

- **FR-U18**: The API MUST let a user delete one receipt and delete all their receipts. Deletion MUST remove the record, file bytes, audit events, LLM call records, and usage records immediately and permanently.
- **FR-U19**: The API MUST let a user delete their account, requiring their password, and MUST remove all their data and tokens.
- **FR-U20**: An in-flight processing task MUST NOT recreate or update data for a receipt that was deleted.

**Changes to existing behavior**

- **FR-U21**: Hash de-duplication MUST be per user and MUST NOT match across users (modifies baseline FR-004).
- **FR-U22**: Original file bytes MUST be deleted as soon as a receipt reaches a terminal state, and no later than a configurable backstop for receipts still in review (modifies baseline FR-036).
- **FR-U23**: Edit, approve, and reject MUST be available only to the owner of a `needs_review` receipt, and the optional review passcode MUST be removed (modifies baseline FR-020, FR-021, FR-023).
- **FR-U24**: The system MUST add per-user daily upload limits and auth endpoint limits to the existing per-IP limits and global LLM budget (extends baseline FR-033).

**Samples and public sheet**

- **FR-U25**: Samples MUST be pinned, ownerless, and read-only. They MUST NOT appear in any user's list, export, or deletion, and MUST NOT be editable by anyone through receipt endpoints.
- **FR-U26**: `POST /samples/{id}/process` MUST keep its baseline contract and return the stored sample receipt id with `cached: true`, without any LLM call.
- **FR-U27**: A stateless validation endpoint MUST accept extracted data and return checks and confidence without storing anything or calling the LLM.
- **FR-U28**: The public Google Sheet MAY contain sample rows only. User receipts MUST NEVER be written to a shared sheet (replaces baseline FR-029 and FR-030 for user data).

**Security and hygiene**

- **FR-U29**: The signing secret, database URL, and other secrets MUST come only from environment variables. Passwords, password hashes, and tokens MUST NOT be logged or returned in error messages.
- **FR-U30**: The database MUST be reachable only by the backend through a least-privilege role, and any data API offered by the database provider MUST be disabled.
- **FR-U31**: New error codes MUST follow the existing error envelope: `unauthenticated`, `token_expired`, `invalid_credentials`, `refresh_invalid`, `email_taken`, `weak_password`, `registration_closed`.

**Migration and rollout**

- **FR-U32**: Before auth-required endpoints go live, the migration MUST delete all existing non-sample receipts, files, audit events, and related call records.
- **FR-U33**: A configuration switch MUST be able to refuse uploads with a clear message, so no new shared data is created before the update ships.
- **FR-U34**: The old public sheet MUST be deleted and replaced with a new sample-only sheet.

### Key Entities (new or changed)

- **User** (new): id, email, password hash, created time, failed-sign-in counters.
- **Refresh Token** (new): hashed value, owner, token family, created, expires, revoked, replaced-by link, last-used time.
- **Receipt** (changed): gains an owner, a sample flag, and a per-user uniqueness rule on file hash. Original file bytes are short-lived.
- **Ledger** (new concept, no new table): the owner's `auto_approved` and `approved` receipts, retained until deleted.
- **User Usage** (new): per-user daily counters used for limits.
- **Sheet Sync Record** (changed): now keyed by sample id.

---

## Success Criteria *(mandatory)*

Baseline success criteria (accuracy, false-pass rate, latency, free-tier behavior) are unchanged. These are added.

- **SC-U01**: An automated isolation suite covers every endpoint that takes a receipt id or returns a list, with users A and B, no token, an expired token, a wrong-key token, and a deleted user's token. It records zero cross-user reads or writes.
- **SC-U02**: A token attack suite (algorithm `none`, wrong secret, wrong issuer or audience, tampering, expiry, missing subject) records zero accepted tokens.
- **SC-U03**: Reusing a rotated refresh token outside the grace window revokes the family in 100% of test cases.
- **SC-U04**: No password, hash, token, or signing secret appears in any response, log line, or error body (test and log inspection). No plaintext password is stored.
- **SC-U05**: An auto-approved or human-approved receipt is in the owner's ledger list the moment its status changes, and in no other user's.
- **SC-U06**: After delete-one, delete-all, or account deletion, no rows, tokens, or file bytes tied to the deleted data remain, verified within one minute.
- **SC-U07**: After a receipt reaches a terminal state, no original file bytes for it remain. Ledger entries survive maintenance runs.
- **SC-U08**: No user receipt row ever appears in the public sample sheet.
- **SC-U09**: After the migration, zero non-sample receipts, files, or audit rows without an owner exist.
- **SC-U10**: Login and token refresh complete in under 1 second on a warm instance.
- **SC-U11**: With the LLM key removed and no token, 100% of samples still return results.
- **SC-U12**: Under a burst of failed logins from one IP or against one account, attempts are slowed or limited and the real owner can still sign in after the delay.

---

## Assumptions

- Accounts are email and password only. There is no email verification and no password recovery in this update.
- Access tokens are signed with a symmetric secret that only this backend verifies. Refresh tokens are opaque and stored hashed.
- The frontend and backend are on different domains, so authentication uses the `Authorization` header, not cookies. The backend sets no cookies.
- "Logging the receipt on their own account" means recording approved receipts in the user's private ledger inside Ledgerline. Writing to the user's own Google Sheet is not part of this update.
- The existing database holds only demo data, so deleting non-sample rows is acceptable.
- A managed Postgres hosts the data and the backend is its only client.
- Users are told that receipt content is processed by a third-party model provider.

---

## Out of Scope

- Password reset and email verification (next update, needs an email service)
- Writing to a user's own Google Sheet or Drive (next update, needs Google consent flow)
- Social sign-in, multi-factor authentication, teams, sharing, roles
- Admin interface
- Changes to extraction, validation rules, or the evaluation harness

---

## Open Questions

- [NEEDS CLARIFICATION: Ledger retention. Recommended default: keep entries until the user deletes them, and delete accounts inactive for 180 days.]
- [NEEDS CLARIFICATION: "Log on their own account" was read as a private ledger inside Ledgerline. If you meant writing to each user's own Google Sheet, that becomes a follow-up update using Google sign-in and incremental consent.]
- [NEEDS CLARIFICATION: User cap and whether registration stays open on the public demo. Recommended default: cap at 200, closable by configuration.]
- [NEEDS CLARIFICATION: Token lifetimes. Recommended default: access token 15 minutes, refresh token 14 days with rotation.]
- [NEEDS CLARIFICATION: Per-user daily upload limit. Recommended default: 20 per day.]

---

## Implementation Constraints (changes to the baseline stack)

- **Added**: PyJWT (pinned algorithm), `argon2-cffi` or `pwdlib` with Argon2, `openpyxl` for XLSX export
- **Added**: Database tables for users and refresh tokens, ownership columns, and per-user usage counters
- **Changed**: Sheets integration limited to sample rows
- **Unchanged**: FastAPI, Groq, PyMuPDF, Pydantic, SQLAlchemy and Alembic, tenacity, slowapi, Docker on Render, GitHub Actions

---

## Review & Acceptance Checklist

- [ ] This update is framed as a change to the existing project, and every changed baseline item is listed
- [ ] Every user story is independently testable and has a priority
- [ ] All `[NEEDS CLARIFICATION]` items are resolved or have an accepted default
- [ ] The isolation suite (SC-U01) and token attack suite (SC-U02) exist before any user-data endpoint is merged
- [ ] The migration (U6) is scheduled before the new endpoints go live
- [ ] Out-of-scope items are clear
