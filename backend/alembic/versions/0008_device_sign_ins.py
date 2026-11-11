"""Editor sign-in: the VS Code extension signs in by showing a code the person approves in
their browser.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_sign_ins",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_code", sa.String(length=9), nullable=False),
        sa.Column("client", sa.String(length=60), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("device_sign_ins", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_device_sign_ins_user_code"), ["user_code"], unique=True)
        batch_op.create_index(batch_op.f("ix_device_sign_ins_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("device_sign_ins", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_device_sign_ins_user_id"))
        batch_op.drop_index(batch_op.f("ix_device_sign_ins_user_code"))
    op.drop_table("device_sign_ins")
