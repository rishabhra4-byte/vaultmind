"""Library folders, pins and the recycle bin.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["folders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("folders", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_folders_parent_id"), ["parent_id"], unique=False)

    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("folder_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_documents_folder_id"), ["folder_id"], unique=False)
        batch_op.create_foreign_key("fk_documents_folder_id", "folders", ["folder_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "pins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "document_id", name="uq_pins_user_document"),
        sa.UniqueConstraint("user_id", "folder_id", name="uq_pins_user_folder"),
    )
    with op.batch_alter_table("pins", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_pins_user_id"), ["user_id"], unique=False)

    op.create_table(
        "deleted_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("original_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("owner_team_id", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=20), nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("deleted_by", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["owner_team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deleted_documents", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_deleted_documents_storage_key"), ["storage_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_deleted_documents_owner_team_id"), ["owner_team_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_deleted_documents_expires_at"), ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("deleted_documents")
    op.drop_table("pins")
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_constraint("fk_documents_folder_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_documents_folder_id"))
        batch_op.drop_column("folder_id")
    op.drop_table("folders")
