"""accounts and per-user receipts

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            email VARCHAR(320) NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            failed_login_count INTEGER NOT NULL DEFAULT 0,
            locked_until TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash VARCHAR(64) NOT NULL,
            family_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            replaced_by UUID REFERENCES refresh_tokens(id) ON DELETE SET NULL,
            last_used_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_refresh_tokens_token_hash "
        "ON refresh_tokens (token_hash)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens (user_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_refresh_tokens_family_id ON refresh_tokens (family_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_usage (
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day DATE NOT NULL,
            uploads INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
        """
    )

    # Destructive cleanup from the old shared model. No owner means no way to keep it safely.
    op.execute("DELETE FROM receipt_files")
    op.execute("DELETE FROM audit_events")
    op.execute("DELETE FROM llm_calls")
    op.execute("DELETE FROM receipts")

    op.execute("ALTER TABLE receipts ADD COLUMN IF NOT EXISTS user_id UUID")
    op.execute(
        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS is_sample BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE receipts ADD COLUMN IF NOT EXISTS sample_id VARCHAR(64)")

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'receipts_user_id_fkey'
            ) THEN
                ALTER TABLE receipts
                ADD CONSTRAINT receipts_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )

    # Replace the old global unique hash index with a per-user one.
    op.execute(
        """
        DO $$
        DECLARE idx record;
        BEGIN
            FOR idx IN
                SELECT indexname FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'receipts'
                  AND indexname <> 'uq_receipts_user_file_sha256'
                  AND indexdef ILIKE '%UNIQUE%'
                  AND indexdef ILIKE '%(file_sha256)%'
            LOOP
                EXECUTE format('DROP INDEX IF EXISTS %I', idx.indexname);
            END LOOP;
        END $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_receipts_file_sha256 ON receipts (file_sha256)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_receipts_user_file_sha256 "
        "ON receipts (user_id, file_sha256) WHERE user_id IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_receipts_user_id ON receipts (user_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_receipts_user_status_uploaded_at "
        "ON receipts (user_id, status, uploaded_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_receipts_user_uploaded_at "
        "ON receipts (user_id, uploaded_at)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_receipts_sample_id ON receipts (sample_id)")

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_receipts_owner_or_sample'
            ) THEN
                ALTER TABLE receipts
                ADD CONSTRAINT ck_receipts_owner_or_sample
                CHECK (
                    (user_id IS NOT NULL AND is_sample = false)
                    OR (user_id IS NULL AND is_sample = true)
                );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE receipts DROP CONSTRAINT IF EXISTS ck_receipts_owner_or_sample")
    op.execute("DROP INDEX IF EXISTS ix_receipts_sample_id")
    op.execute("DROP INDEX IF EXISTS ix_receipts_user_uploaded_at")
    op.execute("DROP INDEX IF EXISTS ix_receipts_user_status_uploaded_at")
    op.execute("DROP INDEX IF EXISTS ix_receipts_user_id")
    op.execute("DROP INDEX IF EXISTS uq_receipts_user_file_sha256")
    op.execute("ALTER TABLE receipts DROP CONSTRAINT IF EXISTS receipts_user_id_fkey")
    op.execute("ALTER TABLE receipts DROP COLUMN IF EXISTS sample_id")
    op.execute("ALTER TABLE receipts DROP COLUMN IF EXISTS is_sample")
    op.execute("ALTER TABLE receipts DROP COLUMN IF EXISTS user_id")
    op.execute("DROP TABLE IF EXISTS user_usage")
    op.execute("DROP TABLE IF EXISTS refresh_tokens")
    op.execute("DROP TABLE IF EXISTS users")
