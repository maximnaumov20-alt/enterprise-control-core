"""Add user token version for immediate access-token revocation.

Revision ID: 0006_user_token_version
Revises: 0005_departments_employees
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_user_token_version"
down_revision: Union[str, None] = "0005_departments_employees"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("users", "token_version", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "token_version")
