"""Create initial asset inventory schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("asset_tag", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("manufacturer", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.Column("assigned_to", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serial_number", name="uq_assets_serial_number"),
    )
    op.create_index("ix_assets_id", "assets", ["id"], unique=False)
    op.create_index("ix_assets_name", "assets", ["name"], unique=False)
    op.create_index("ix_assets_asset_tag", "assets", ["asset_tag"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_assets_asset_tag", table_name="assets")
    op.drop_index("ix_assets_name", table_name="assets")
    op.drop_index("ix_assets_id", table_name="assets")
    op.drop_table("assets")
