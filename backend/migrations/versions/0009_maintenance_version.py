"""Add optimistic-locking version to maintenance requests.

Revision ID: 0009_maintenance_version
Revises: 0008_license_record_version
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_maintenance_version"
down_revision: Union[str, None] = "0008_license_record_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "maintenance_requests",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("maintenance_requests", "version")
