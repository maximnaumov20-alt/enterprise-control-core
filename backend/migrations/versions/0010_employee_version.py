"""Add optimistic-locking version to employees.

Revision ID: 0010_employee_version
Revises: 0009_maintenance_version
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_employee_version"
down_revision: Union[str, None] = "0009_maintenance_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("employees", "version")
