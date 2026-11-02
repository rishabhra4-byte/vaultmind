"""Vectors for search by meaning.

Optional (VAULTMIND_EMBEDDINGS_URL): each passage's vector from the configured embedding
model, so passages that say the same thing in other words can be found.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chunk_id"),
    )


def downgrade() -> None:
    op.drop_table("chunk_embeddings")
