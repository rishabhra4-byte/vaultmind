"""Feedback on answers.

People can mark an answer helpful or not, with a reason and a note; administrators review the
unhelpful ones, and they can be exported as drafts for the answer-quality benchmark.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "user_id"),
    )
    with op.batch_alter_table("answer_feedback", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_answer_feedback_message_id"), ["message_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_answer_feedback_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("answer_feedback", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_answer_feedback_user_id"))
        batch_op.drop_index(batch_op.f("ix_answer_feedback_message_id"))
    op.drop_table("answer_feedback")
