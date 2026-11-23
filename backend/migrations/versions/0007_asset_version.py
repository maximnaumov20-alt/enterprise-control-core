"""Add optimistic-locking version to assets.

Revision ID: 0007_asset_version
Revises: 0006_user_token_version
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_asset_version"
down_revision: Union[str, None] = "0006_user_token_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("assets", "version")
