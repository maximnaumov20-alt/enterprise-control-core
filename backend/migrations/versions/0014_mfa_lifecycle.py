"""Persist tenant-scoped MFA factors, login challenges and recovery codes.

Revision ID: 0014_mfa_lifecycle
Revises: 0013_auth_sessions
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0014_mfa_lifecycle"
down_revision: Union[str, None] = "0013_auth_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mfa_factors",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_secret", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pending_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_counter", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'enabled', 'disabled')", name="ck_mfa_factors_status"),
        sa.CheckConstraint("last_used_counter IS NULL OR last_used_counter >= 0", name="ck_mfa_factors_counter"),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"], ["users.organization_id", "users.id"],
            ondelete="CASCADE", name="fk_mfa_factors_user_same_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_mfa_factors_org_user"),
    )
    op.create_index("ix_mfa_factors_organization_id", "mfa_factors", ["organization_id"])
    op.create_index("ix_mfa_factors_user_id", "mfa_factors", ["user_id"])

    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"], ["users.organization_id", "users.id"],
            ondelete="CASCADE", name="fk_mfa_recovery_codes_user_same_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", "code_hash", name="uq_mfa_recovery_codes_hash"),
    )
    op.create_index("ix_mfa_recovery_codes_organization_id", "mfa_recovery_codes", ["organization_id"])
    op.create_index("ix_mfa_recovery_codes_user_id", "mfa_recovery_codes", ["user_id"])

    op.create_table(
        "mfa_challenges",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 5", name="ck_mfa_challenges_attempts"),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"], ["users.organization_id", "users.id"],
            ondelete="CASCADE", name="fk_mfa_challenges_user_same_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_mfa_challenges_token_hash"),
    )
    op.create_index("ix_mfa_challenges_organization_id", "mfa_challenges", ["organization_id"])
    op.create_index("ix_mfa_challenges_user_id", "mfa_challenges", ["user_id"])
    op.create_index("ix_mfa_challenges_expires_at", "mfa_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("mfa_challenges")
    op.drop_table("mfa_recovery_codes")
    op.drop_table("mfa_factors")
