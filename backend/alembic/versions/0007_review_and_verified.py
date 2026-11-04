"""The fix-it queue and verified answers.

review_cases: real questions an administrator has reviewed (what answers them, or that nothing
does), checked again as the person who asked. verified_answers: approved answers to common
questions, with an owning team and a review date.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=320), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("asked_by_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expect", sa.String(length=20), nullable=False),
        sa.Column("doc_ids", sa.JSON(), nullable=False),
        sa.Column("says", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("resolved_by_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_check", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["asked_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "verified_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("phrasings", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("doc_ids", sa.JSON(), nullable=False),
        sa.Column("doc_versions", sa.JSON(), nullable=False),
        sa.Column("owner_team_id", sa.Integer(), nullable=False),
        sa.Column("verified_by_id", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_days", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["verified_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("verified_answers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_verified_answers_owner_team_id"), ["owner_team_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("verified_answers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_verified_answers_owner_team_id"))
    op.drop_table("verified_answers")
    op.drop_table("review_cases")
