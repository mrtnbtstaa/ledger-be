"""receipt list indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS keeps a fresh 0001 (metadata create_all) and an upgraded old DB both valid.
    op.execute("CREATE INDEX IF NOT EXISTS ix_receipts_uploaded_at ON receipts (uploaded_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_receipts_data_category ON receipts ((data ->> 'category'))")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_receipts_data_total "
        "ON receipts (((data ->> 'total')::numeric) DESC NULLS LAST)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_receipts_data_total")
    op.execute("DROP INDEX IF EXISTS ix_receipts_data_category")
    op.execute("DROP INDEX IF EXISTS ix_receipts_uploaded_at")
