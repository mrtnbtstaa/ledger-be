"""sample-only sheet sync

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sheet_sync")
    op.execute(
        """
        CREATE TABLE sheet_sync (
            sample_id VARCHAR(64) PRIMARY KEY,
            state VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sheet_sync_state_updated_at "
        "ON sheet_sync (state, updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sheet_sync")
    op.execute(
        """
        CREATE TABLE sheet_sync (
            receipt_id UUID PRIMARY KEY REFERENCES receipts(id) ON DELETE CASCADE,
            state VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sheet_sync_state_updated_at "
        "ON sheet_sync (state, updated_at)"
    )
