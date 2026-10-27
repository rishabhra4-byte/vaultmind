"""At most one pending access request per person per document.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PENDING = sa.text("status = 'pending'")


def upgrade() -> None:
    # Older versions could store duplicates; keep the oldest pending one and cancel the rest.
    op.execute(
        """
        UPDATE access_requests SET status = 'cancelled'
        WHERE status = 'pending' AND id NOT IN (
            SELECT MIN(id) FROM access_requests WHERE status = 'pending' GROUP BY document_id, requester_id
        )
        """
    )
    with op.batch_alter_table("access_requests") as batch:
        batch.create_index(
            "uq_one_pending_request",
            ["document_id", "requester_id"],
            unique=True,
            sqlite_where=PENDING,
            postgresql_where=PENDING,
            if_not_exists=True,  # databases built from the models directly already have it
        )


def downgrade() -> None:
    with op.batch_alter_table("access_requests") as batch:
        batch.drop_index("uq_one_pending_request", sqlite_where=PENDING, postgresql_where=PENDING)
