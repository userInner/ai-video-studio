"""add storyboards and media assets

Revision ID: 27a2e1c4b9d0
Revises: f3c7c92d90a1
Create Date: 2026-08-20 22:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "27a2e1c4b9d0"
down_revision: Union[str, Sequence[str], None] = "f3c7c92d90a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "storyboard_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("script_version_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["script_version_id"], ["script_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_storyboard_versions_project_id"), "storyboard_versions", ["project_id"], unique=False)
    op.create_index(op.f("ix_storyboard_versions_script_version_id"), "storyboard_versions", ["script_version_id"], unique=False)
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("script_version_id", sa.String(length=32), nullable=False),
        sa.Column("scene_index", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=48), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["script_version_id"], ["script_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_assets_kind"), "media_assets", ["kind"], unique=False)
    op.create_index(op.f("ix_media_assets_project_id"), "media_assets", ["project_id"], unique=False)
    op.create_index(op.f("ix_media_assets_script_version_id"), "media_assets", ["script_version_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_media_assets_script_version_id"), table_name="media_assets")
    op.drop_index(op.f("ix_media_assets_project_id"), table_name="media_assets")
    op.drop_index(op.f("ix_media_assets_kind"), table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_index(op.f("ix_storyboard_versions_script_version_id"), table_name="storyboard_versions")
    op.drop_index(op.f("ix_storyboard_versions_project_id"), table_name="storyboard_versions")
    op.drop_table("storyboard_versions")
