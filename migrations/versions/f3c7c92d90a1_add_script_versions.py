"""add script versions

Revision ID: f3c7c92d90a1
Revises: bef3593122f0
Create Date: 2026-08-20 21:10:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3c7c92d90a1"
down_revision: Union[str, Sequence[str], None] = "bef3593122f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "script_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("production_card_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["production_card_id"], ["production_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_script_versions_production_card_id"), "script_versions", ["production_card_id"], unique=False)
    op.create_index(op.f("ix_script_versions_project_id"), "script_versions", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_script_versions_project_id"), table_name="script_versions")
    op.drop_index(op.f("ix_script_versions_production_card_id"), table_name="script_versions")
    op.drop_table("script_versions")
