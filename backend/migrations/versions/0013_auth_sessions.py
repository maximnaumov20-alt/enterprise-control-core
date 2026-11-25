"""Persist per-login authentication sessions.

Revision ID: 0013_auth_sessions
Revises: 0012_user_version
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_auth_sessions"
down_revision: Union[str, None] = "0012_user_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            ondelete="CASCADE",
            name="fk_auth_sessions_user_same_org",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_sessions_organization_id", "auth_sessions", ["organization_id"])
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_organization_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
