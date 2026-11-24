"""Add optimistic-locking version to users.

Revision ID: 0012_user_version
Revises: 0011_department_version
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_user_version"
down_revision: Union[str, None] = "0011_department_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("users", "version")
