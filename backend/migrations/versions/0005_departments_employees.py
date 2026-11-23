"""Add department and employee directory.

Revision ID: 0005_departments_employees
Revises: 0004_maintenance_requests
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_departments_employees"
down_revision: Union[str, None] = "0004_maintenance_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "id", name="uq_departments_org_id_id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_departments_org_name"),
        sa.UniqueConstraint("organization_id", "code", name="uq_departments_org_code"),
    )
    op.create_index("ix_departments_organization_id", "departments", ["organization_id"])

    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("employee_number", sa.String(length=40), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("job_title", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "employee_number", name="uq_employees_org_number"),
        sa.UniqueConstraint("organization_id", "email", name="uq_employees_org_email"),
        sa.ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_employees_department_same_org",
        ),
    )
    op.create_index("ix_employees_organization_id", "employees", ["organization_id"])
    op.create_index("ix_employees_department_id", "employees", ["department_id"])
    op.create_index("ix_employees_full_name", "employees", ["full_name"])


def downgrade() -> None:
    op.drop_index("ix_employees_full_name", table_name="employees")
    op.drop_index("ix_employees_department_id", table_name="employees")
    op.drop_index("ix_employees_organization_id", table_name="employees")
    op.drop_table("employees")
    op.drop_index("ix_departments_organization_id", table_name="departments")
    op.drop_table("departments")
