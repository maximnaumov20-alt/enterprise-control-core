"""Add organizations, users, tenant scoping and audit events.

Revision ID: 0002_tenants_auth_audit
Revises: 0001_initial
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_tenants_auth_audit"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    bind = op.get_bind()
    organization_id = bind.execute(
        sa.text(
            "INSERT INTO organizations (name, created_at) "
            "VALUES (:name, CURRENT_TIMESTAMP) RETURNING id"
        ),
        {"name": "Default Organization"},
    ).scalar_one()

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.drop_index("ix_assets_asset_tag", table_name="assets")
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
    bind.execute(
        sa.text("UPDATE assets SET organization_id = :organization_id"),
        {"organization_id": organization_id},
    )
    with op.batch_alter_table("assets") as batch:
        batch.alter_column("organization_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_assets_organization_id", "organizations", ["organization_id"], ["id"],
            ondelete="RESTRICT",
        )
        batch.drop_constraint("uq_assets_serial_number", type_="unique")
        batch.create_unique_constraint("uq_assets_org_asset_tag", ["organization_id", "asset_tag"])
        batch.create_unique_constraint("uq_assets_org_serial_number", ["organization_id", "serial_number"])
    op.create_index("ix_assets_organization_id", "assets", ["organization_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_organization_id", "audit_events", ["organization_id"])
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_organization_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_assets_organization_id", table_name="assets")
    with op.batch_alter_table("assets") as batch:
        batch.drop_constraint("uq_assets_org_serial_number", type_="unique")
        batch.drop_constraint("uq_assets_org_asset_tag", type_="unique")
        batch.drop_constraint("fk_assets_organization_id", type_="foreignkey")
        batch.drop_column("organization_id")
        batch.create_unique_constraint("uq_assets_serial_number", ["serial_number"])
    op.create_index("ix_assets_asset_tag", "assets", ["asset_tag"], unique=True)
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
