"""Add optimistic-locking record version to software licenses.

Revision ID: 0008_software_license_record_version
Revises: 0007_asset_version
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_software_license_record_version"
down_revision: Union[str, None] = "0007_asset_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "software_licenses",
        sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("software_licenses", "record_version")
