"""sheet sync outbox

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sheet_sync (
            receipt_id UUID PRIMARY KEY REFERENCES receipts(id) ON DELETE CASCADE,
            state VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_sheet_sync_state_updated_at ON sheet_sync (state, updated_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sheet_sync")
