from __future__ import annotations

import csv
from contextlib import asynccontextmanager
import io
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Callable
from uuid import uuid4

import jwt
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pwdlib import PasswordHash
from redis import Redis
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import (
    CheckConstraint, Date, DateTime, Enum as SAEnum, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, Text, UniqueConstraint,
    create_engine, select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/ecc.db")
if DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
SECRET_KEY = os.getenv("ECC_SECRET_KEY")
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "20"))
AUTH_RATE_LIMIT_MAX = int(os.getenv("AUTH_RATE_LIMIT_MAX", "10"))
AUTH_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60"))
if AUTH_RATE_LIMIT_MAX < 1 or AUTH_RATE_LIMIT_WINDOW_SECONDS < 1:
    raise RuntimeError("Authentication rate-limit settings must be positive integers.")
if SECRET_KEY and "REPLACE_WITH" in SECRET_KEY:
    raise RuntimeError("ECC_SECRET_KEY is still a placeholder; configure a unique random secret.")
if SECRET_KEY and len(SECRET_KEY.encode("utf-8")) < 32:
    raise RuntimeError("ECC_SECRET_KEY must contain at least 32 bytes.")
if not 5 <= ACCESS_TOKEN_MINUTES <= 60:
    raise RuntimeError("ACCESS_TOKEN_MINUTES must be between 5 and 60.")
class LoginRateLimiter:
    """Fixed-window login limiter. Redis is shared across API workers in production."""

    def __init__(self, max_attempts: int, window_seconds: int, redis_client: Redis | None = None):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.redis_client = redis_client
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[int, float]] = {}

    def check(self, key: str) -> int | None:
        if self.redis_client is not None:
            count, ttl = self.redis_client.eval(
                """
                local count = redis.call('INCR', KEYS[1])
                if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
                return {count, redis.call('TTL', KEYS[1])}
                """,
                1, key, self.window_seconds,
            )
            return max(int(ttl), 1) if int(count) > self.max_attempts else None

        now = time.monotonic()
        with self._lock:
            count, expires_at = self._buckets.get(key, (0, now + self.window_seconds))
            if now >= expires_at:
                count, expires_at = 0, now + self.window_seconds
            count += 1
            self._buckets[key] = (count, expires_at)
            if count > self.max_attempts:
                return max(int(expires_at - now + 0.999), 1)
        return None


redis_url = os.getenv("REDIS_URL")
redis_client = Redis.from_url(redis_url, decode_responses=True) if redis_url else None
login_rate_limiter = LoginRateLimiter(
    AUTH_RATE_LIMIT_MAX, AUTH_RATE_LIMIT_WINDOW_SECONDS, redis_client
)

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


class Base(DeclarativeBase):
    pass


class AssetStatus(str, Enum):
    active = "active"
    in_repair = "in_repair"
    retired = "retired"


class UserRole(str, Enum):
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class MaintenancePriority(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class MaintenanceStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    cancelled = "cancelled"


class SoftwareLicenseType(str, Enum):
    subscription = "subscription"
    perpetual = "perpetual"
    oem = "oem"
    site = "site"


class SoftwareLicenseStatus(str, Enum):
    active = "active"
    suspended = "suspended"
    retired = "retired"


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_departments_org_id_id"),
        UniqueConstraint("organization_id", "name", name="uq_departments_org_name"),
        UniqueConstraint("organization_id", "code", name="uq_departments_org_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __mapper_args__ = {"version_id_col": version}


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("organization_id", "employee_number", name="uq_employees_org_number"),
        UniqueConstraint("organization_id", "email", name="uq_employees_org_email"),
        ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_employees_department_same_org",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    employee_number: Mapped[str] = mapped_column(String(40), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": version}


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_users_org_id_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, native_enum=False, length=16), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __mapper_args__ = {"version_id_col": version}


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            ondelete="CASCADE",
            name="fk_auth_sessions_user_same_org",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("organization_id", "asset_tag", name="uq_assets_org_asset_tag"),
        UniqueConstraint("organization_id", "serial_number", name="uq_assets_org_serial_number"),
        UniqueConstraint("organization_id", "id", name="uq_assets_org_id_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    asset_tag: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[AssetStatus] = mapped_column(
        SAEnum(AssetStatus, native_enum=False), default=AssetStatus.active, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": version}


class MaintenanceRequest(Base):
    __tablename__ = "maintenance_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "asset_id"],
            ["assets.organization_id", "assets.id"],
            ondelete="RESTRICT",
            name="fk_maintenance_asset_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "requested_by_user_id"],
            ["users.organization_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_maintenance_requester_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assigned_to_user_id"],
            ["users.organization_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_maintenance_assignee_same_org",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    requested_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    assigned_to_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[MaintenancePriority] = mapped_column(
        SAEnum(MaintenancePriority, native_enum=False, length=8),
        default=MaintenancePriority.normal,
        nullable=False,
    )
    status: Mapped[MaintenanceStatus] = mapped_column(
        SAEnum(MaintenanceStatus, native_enum=False, length=16),
        default=MaintenanceStatus.open,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": version}


class SoftwareLicense(Base):
    __tablename__ = "software_licenses"
    __table_args__ = (
        UniqueConstraint("organization_id", "license_reference", name="uq_software_org_reference"),
        CheckConstraint("seats_total >= 1", name="ck_software_seats_total_positive"),
        CheckConstraint("seats_used >= 0 AND seats_used <= seats_total", name="ck_software_seats_used_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    publisher: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    license_type: Mapped[SoftwareLicenseType] = mapped_column(
        SAEnum(SoftwareLicenseType, native_enum=False, length=16), nullable=False
    )
    license_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    seats_total: Mapped[int] = mapped_column(Integer, nullable=False)
    seats_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    purchased_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[SoftwareLicenseStatus] = mapped_column(
        SAEnum(SoftwareLicenseStatus, native_enum=False, length=16),
        default=SoftwareLicenseStatus.active,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    record_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": record_version}


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class AssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    asset_tag: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    manufacturer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    assigned_to: str | None = Field(default=None, max_length=160)
    status: AssetStatus = AssetStatus.active


class AssetUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    manufacturer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    assigned_to: str | None = Field(default=None, max_length=160)
    status: AssetStatus | None = None


class AssetBulkUpdate(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=100)
    versions: dict[int, int] = Field(min_length=1, max_length=100)
    status: AssetStatus | None = None
    assigned_to: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_bulk_update(self):
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("asset_ids must not contain duplicates")
        if set(self.versions) != set(self.asset_ids):
            raise ValueError("versions must contain exactly one version for every asset_id")
        if any(version < 1 for version in self.versions.values()):
            raise ValueError("asset versions must be positive integers")
        if any(asset_id <= 0 for asset_id in self.asset_ids):
            raise ValueError("asset_ids must contain positive integers")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        if not ({"status", "assigned_to"} & self.model_fields_set):
            raise ValueError("At least one field to update is required")
        if self.assigned_to is not None:
            self.assigned_to = self.assigned_to.strip() or None
        return self


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    asset_tag: str
    category: str
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    assigned_to: str | None
    status: AssetStatus
    created_at: datetime
    updated_at: datetime
    version: int


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40)


class DepartmentUpdate(BaseModel):
    expected_version: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, min_length=1, max_length=40)


class DepartmentRead(BaseModel):
    id: int
    name: str
    code: str
    created_at: datetime
    version: int


class EmployeeCreate(BaseModel):
    employee_number: str = Field(min_length=1, max_length=40)
    full_name: str = Field(min_length=1, max_length=160)
    department_id: int | None = Field(default=None, gt=0)
    email: EmailStr | None = None
    job_title: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)


class EmployeeUpdate(BaseModel):
    expected_version: int = Field(gt=0)
    employee_number: str | None = Field(default=None, min_length=1, max_length=40)
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    department_id: int | None = Field(default=None, gt=0)
    email: EmailStr | None = None
    job_title: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    is_active: bool | None = None


class EmployeeRead(BaseModel):
    id: int
    employee_number: str
    full_name: str
    department_id: int | None
    email: EmailStr | None
    job_title: str | None
    phone: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int


class MaintenanceRequestCreate(BaseModel):
    asset_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    priority: MaintenancePriority = MaintenancePriority.normal


class MaintenanceRequestUpdate(BaseModel):
    expected_version: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    priority: MaintenancePriority | None = None
    status: MaintenanceStatus | None = None
    assigned_to_user_id: int | None = Field(default=None, gt=0)


class MaintenanceRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_id: int
    requested_by_user_id: int
    assigned_to_user_id: int | None
    title: str
    description: str
    priority: MaintenancePriority
    status: MaintenanceStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    version: int


class SoftwareLicenseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    publisher: str | None = Field(default=None, max_length=120)
    version: str | None = Field(default=None, max_length=80)
    license_type: SoftwareLicenseType
    license_reference: str | None = Field(default=None, max_length=120)
    seats_total: int = Field(ge=1, le=1000000)
    seats_used: int = Field(default=0, ge=0, le=1000000)
    purchased_at: date | None = None
    expires_at: date | None = None
    status: SoftwareLicenseStatus = SoftwareLicenseStatus.active
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_seat_counts(self):
        if self.seats_used > self.seats_total:
            raise ValueError("seats_used cannot exceed seats_total")
        if self.purchased_at and self.expires_at and self.expires_at < self.purchased_at:
            raise ValueError("expires_at cannot be earlier than purchased_at")
        return self


class SoftwareLicenseUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    publisher: str | None = Field(default=None, max_length=120)
    version: str | None = Field(default=None, max_length=80)
    license_type: SoftwareLicenseType | None = None
    license_reference: str | None = Field(default=None, max_length=120)
    seats_total: int | None = Field(default=None, ge=1, le=1000000)
    seats_used: int | None = Field(default=None, ge=0, le=1000000)
    purchased_at: date | None = None
    expires_at: date | None = None
    status: SoftwareLicenseStatus | None = None
    notes: str | None = Field(default=None, max_length=4000)


class SoftwareLicenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    publisher: str | None
    version: str | None
    license_type: SoftwareLicenseType
    license_reference: str | None
    seats_total: int
    seats_used: int
    purchased_at: date | None
    expires_at: date | None
    status: SoftwareLicenseStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime
    record_version: int


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=128)
    role: UserRole


class UserUpdate(BaseModel):
    expected_version: int = Field(gt=0)
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    role: UserRole | None = None
    is_active: bool | None = None


class UserRead(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    organization_id: int
    created_at: datetime
    version: int


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


class CurrentUserRead(UserRead):
    organization_name: str


def get_session():
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_access_token(user: User, session_id: str | None = None) -> str:
    if not SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured: set ECC_SECRET_KEY",
        )
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "type": "access",
        "ver": user.token_version,
        "iss": "ecc-api",
        "aud": "ecc-client",
    }
    if session_id is not None:
        payload["sid"] = session_id
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: SessionDep,
) -> User:
    if not SECRET_KEY:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=["HS256"],
            issuer="ecc-api",
            audience="ecc-client",
        )
        if payload.get("type") != "access":
            raise unauthorized
        user_id = int(payload["sub"])
        token_version = int(payload["ver"])
        session_id = payload.get("sid")
        if session_id is not None and not isinstance(session_id, str):
            raise unauthorized
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise unauthorized
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.token_version != token_version:
        raise unauthorized
    if session_id is not None:
        auth_session = session.get(AuthSession, session_id)
        now = datetime.now(timezone.utc)
        expires_at = auth_session.expires_at if auth_session is not None else now
        last_seen_at = auth_session.last_seen_at if auth_session is not None else now
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
        if (
            auth_session is None
            or auth_session.user_id != user.id
            or auth_session.organization_id != user.organization_id
            or auth_session.token_version != token_version
            or auth_session.revoked_at is not None
            or expires_at <= now
        ):
            raise unauthorized
        user.current_auth_session_id = auth_session.id
        if (now - last_seen_at).total_seconds() >= 300:
            auth_session.last_seen_at = now
            session.commit()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable:
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dependency


def record_audit(
    session: Session,
    user: User,
    action: str,
    entity_type: str,
    entity_id: int | str,
    details: dict | None = None,
) -> None:
    session.add(AuditEvent(
        organization_id=user.organization_id,
        actor_user_id=user.id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=details or {},
    ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate active and previous MFA keys before serving any requests.
    # Import locally to avoid the app <-> MFA router import cycle.
    from app.mfa_crypto import MFAEncryptionKeyring

    MFAEncryptionKeyring.from_environment(os.environ)
    yield


app = FastAPI(
    title="Enterprise Control Center API",
    version="0.2.0",
    description="Early-development API for organization-scoped IT asset inventory.",
    lifespan=lifespan,
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def disable_api_response_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/health", tags=["system"])
def health():
    """Liveness probe: confirms that the API process is responding."""
    return {"status": "ok", "service": "enterprise-control-center-api"}


@app.get("/ready", tags=["system"])
def readiness():
    """Readiness probe: checks required runtime dependencies without exposing internals."""
    checks = {"database": "ok", "redis": "not_configured"}

    try:
        with engine.connect() as connection:
            connection.execute(select(1))
    except Exception:
        checks["database"] = "unavailable"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": checks},
        )

    if redis_client is not None:
        try:
            redis_client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "not_ready", "checks": checks},
            )

    return {"status": "ready", "checks": checks}


@app.post("/api/v1/auth/token", response_model=TokenRead, tags=["authentication"])
def login(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
):
    # X-Real-IP is set by the private Nginx gateway, which overwrites client input.
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    try:
        retry_after = login_rate_limiter.check(f"ecc:auth:login:{client_ip}")
    except Exception:
        # Do not silently disable brute-force protection when the shared store fails.
        raise HTTPException(status_code=503, detail="Authentication temporarily unavailable")
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = session.scalar(select(User).where(User.email == form.username.strip().lower()))
    if user is None or not user.is_active or not password_hash.verify(form.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    now = datetime.now(timezone.utc)
    auth_session = AuthSession(
        id=str(uuid4()),
        organization_id=user.organization_id,
        user_id=user.id,
        token_version=user.token_version,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    )
    session.add(auth_session)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=503, detail="Unable to create an authenticated session")
    return TokenRead(
        access_token=create_access_token(user, auth_session.id),
        expires_in=ACCESS_TOKEN_MINUTES * 60,
    )


@app.post("/api/v1/auth/change-password", tags=["authentication"])
def change_password(
    payload: PasswordChange,
    session: SessionDep,
    user: CurrentUser,
):
    if not password_hash.verify(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    try:
        user.password_hash = password_hash.hash(payload.new_password)
        user.token_version += 1
        record_audit(session, user, "user.password_changed", "user", user.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Password change conflicts with current account records",
        )
    return {"status": "password_updated"}


@app.get("/api/v1/auth/sessions", response_model=list[AuthSessionRead], tags=["authentication"])
def list_auth_sessions(session: SessionDep, user: CurrentUser):
    return list(session.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.organization_id == user.organization_id,
        )
        .order_by(AuthSession.created_at.desc())
    ).all())


@app.delete("/api/v1/auth/sessions/{session_id}", tags=["authentication"])
def revoke_auth_session(session_id: str, session: SessionDep, user: CurrentUser):
    auth_session = session.scalar(select(AuthSession).where(
        AuthSession.id == session_id,
        AuthSession.user_id == user.id,
        AuthSession.organization_id == user.organization_id,
    ))
    if auth_session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(timezone.utc)
        try:
            record_audit(session, user, "auth_session.revoked", "auth_session", auth_session.id)
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail="Session revocation conflicts with current records")
    return {"status": "revoked"}


@app.post("/api/v1/auth/logout", tags=["authentication"])
def logout(session: SessionDep, user: CurrentUser):
    """Revoke only the authenticated browser session."""
    session_id = getattr(user, "current_auth_session_id", None)
    if session_id is None:
        # Compatibility for pre-session tokens: invalidate the account's tokens.
        user.token_version += 1
        action = "user.logged_out"
        entity_type = "user"
        entity_id = user.id
    else:
        auth_session = session.get(AuthSession, session_id)
        if auth_session is None:
            raise HTTPException(status_code=401, detail="Invalid or expired authentication token")
        auth_session.revoked_at = datetime.now(timezone.utc)
        action = "auth_session.logged_out"
        entity_type = "auth_session"
        entity_id = session_id
    try:
        record_audit(session, user, action, entity_type, entity_id)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Logout conflicts with current account records")
    return {"status": "logged_out", "revoked_sessions": "current" if session_id else "all"}


@app.post("/api/v1/auth/logout-all", tags=["authentication"])
def logout_all(session: SessionDep, user: CurrentUser):
    """Revoke every session and invalidate legacy access tokens for the account."""
    now = datetime.now(timezone.utc)
    user.token_version += 1
    session.query(AuthSession).filter(
        AuthSession.user_id == user.id,
        AuthSession.organization_id == user.organization_id,
        AuthSession.revoked_at.is_(None),
    ).update({AuthSession.revoked_at: now}, synchronize_session=False)
    try:
        record_audit(session, user, "user.logged_out_all", "user", user.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Logout conflicts with current account records")
    return {"status": "logged_out", "revoked_sessions": "all"}


@app.get("/api/v1/auth/me", response_model=CurrentUserRead, tags=["authentication"])
def get_me(user: CurrentUser, session: SessionDep):
    organization = session.get(Organization, user.organization_id)
    return CurrentUserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        organization_id=user.organization_id,
        created_at=user.created_at,
        version=user.version,
        organization_name=organization.name if organization else "",
    )


@app.post(
    "/api/v1/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    tags=["users"],
)
def create_user(
    payload: UserCreate,
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(UserRole.admin))],
):
    normalized_email = str(payload.email).lower()
    normalized_name = payload.full_name.strip()
    if not normalized_name:
        raise HTTPException(status_code=422, detail="Full name cannot be blank")
    if session.scalar(select(User).where(User.email == normalized_email)):
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = User(
        organization_id=admin.organization_id,
        email=normalized_email,
        full_name=normalized_name,
        password_hash=password_hash.hash(payload.password),
        role=payload.role,
    )
    session.add(user)
    try:
        session.flush()
        record_audit(session, admin, "user.created", "user", user.id, {"role": user.role.value})
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Email is already registered")
    session.refresh(user)
    return user


@app.patch("/api/v1/users/{user_id}", response_model=UserRead, tags=["users"])
def update_user(
    user_id: int,
    payload: UserUpdate,
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(UserRole.admin))],
):
    target = session.scalar(select(User).where(
        User.id == user_id, User.organization_id == admin.organization_id
    ))
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.expected_version != target.version:
        raise HTTPException(status_code=409, detail="User has changed; reload it before updating")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if not changes:
        return target
    for required_field in ("full_name", "role", "is_active"):
        if required_field in changes and changes[required_field] is None:
            raise HTTPException(status_code=422, detail=f"{required_field} cannot be null")
    if "full_name" in changes:
        changes["full_name"] = changes["full_name"].strip()
        if not changes["full_name"]:
            raise HTTPException(status_code=422, detail="Full name cannot be blank")
    if target.id == admin.id and (
        changes.get("is_active") is False or changes.get("role") not in (None, UserRole.admin)
    ):
        raise HTTPException(status_code=422, detail="You cannot disable or demote your own admin account")
    changes = {
        field: value for field, value in changes.items()
        if getattr(target, field) != value
    }
    if not changes:
        return target
    if "is_active" in changes:
        target.token_version += 1
    for field, value in changes.items():
        setattr(target, field, value)
    try:
        record_audit(session, admin, "user.updated", "user", target.id, {"fields": sorted(changes)})
        session.commit()
    except StaleDataError:
        session.rollback()
        raise HTTPException(status_code=409, detail="User has changed; reload it before updating")
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="User update conflicts with current organization records")
    session.refresh(target)
    return target


@app.get("/api/v1/users", response_model=list[UserRead], tags=["users"])
def list_users(
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(UserRole.admin))],
):
    return list(session.scalars(
        select(User).where(User.organization_id == admin.organization_id).order_by(User.id)
    ).all())


@app.post(
    "/api/v1/assets",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
def create_asset(payload: AssetCreate, session: SessionDep, user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))]):
    asset = Asset(organization_id=user.organization_id, **payload.model_dump())
    session.add(asset)
    try:
        session.flush()
        record_audit(session, user, "asset.created", "asset", asset.id, {
            "asset_tag": asset.asset_tag, "name": asset.name, "category": asset.category,
            "status": asset.status.value,
        })
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Asset creation conflicts with existing organization records")
    session.refresh(asset)
    return asset


@app.get("/api/v1/assets", response_model=list[AssetRead], tags=["assets"])
def list_assets(
    session: SessionDep,
    user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    search: Annotated[str | None, Query(max_length=120)] = None,
):
    statement = select(Asset).where(Asset.organization_id == user.organization_id)
    if search:
        statement = statement.where(Asset.name.ilike(f"%{search}%"))
    statement = statement.order_by(Asset.id.desc()).offset(offset).limit(limit)
    return list(session.scalars(statement).all())



@app.patch("/api/v1/assets/bulk", tags=["assets"])
def bulk_update_assets(
    payload: AssetBulkUpdate,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    assets = list(session.scalars(
        select(Asset)
        .where(
            Asset.organization_id == user.organization_id,
            Asset.id.in_(payload.asset_ids),
        )
        .order_by(Asset.id)
    ).all())
    if len(assets) != len(payload.asset_ids):
        raise HTTPException(status_code=404, detail="One or more assets were not found")

    if any(payload.versions.get(asset.id) != asset.version for asset in assets):
        raise HTTPException(status_code=409, detail="One or more assets have changed. Reload the assets and retry.")
    requested = payload.model_dump(exclude_unset=True, exclude={"asset_ids", "versions"})
    changed_count = 0
    try:
        for asset in assets:
            changes: dict[str, dict[str, str | None]] = {}
            for field, value in requested.items():
                old_value = getattr(asset, field)
                old_value = old_value.value if isinstance(old_value, Enum) else old_value
                new_value = value.value if isinstance(value, Enum) else value
                if old_value != new_value:
                    changes[field] = {"old": old_value, "new": new_value}
                    setattr(asset, field, value)
            if changes:
                record_audit(
                    session, user, "asset.updated", "asset", asset.id,
                    {"changes": changes, "source": "bulk"},
                )
                changed_count += 1
        session.commit()
    except (IntegrityError, StaleDataError):
        session.rollback()
        raise HTTPException(status_code=409, detail="Bulk update conflicts with existing data or stale asset versions")

    for asset in assets:
        session.refresh(asset)
    return {
        "selected": len(assets),
        "updated": changed_count,
        "assets": [AssetRead.model_validate(asset).model_dump(mode="json") for asset in assets],
    }


ASSET_CSV_COLUMNS = (
    "name", "asset_tag", "category", "manufacturer", "model",
    "serial_number", "assigned_to", "status",
)
MAX_ASSET_IMPORT_BYTES = 2 * 1024 * 1024
MAX_ASSET_IMPORT_ROWS = 5000


def _spreadsheet_safe(value: object) -> str:
    if isinstance(value, Enum):
        value = value.value
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


@app.get("/api/v1/assets/export", tags=["assets"])
def export_assets(
    session: SessionDep,
    user: CurrentUser,
):
    assets = session.scalars(
        select(Asset)
        .where(Asset.organization_id == user.organization_id)
        .order_by(Asset.id)
    ).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(ASSET_CSV_COLUMNS)
    for asset in assets:
        writer.writerow([
            _spreadsheet_safe(getattr(asset, column))
            for column in ASSET_CSV_COLUMNS
        ])
    content = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ecc-assets.csv"'},
    )


@app.post("/api/v1/assets/import", tags=["assets"])
async def import_assets(
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
    file: UploadFile = File(...),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a .csv file")
    raw = await file.read(MAX_ASSET_IMPORT_BYTES + 1)
    if len(raw) > MAX_ASSET_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="CSV file exceeds the 2 MiB limit")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must use UTF-8 encoding")
    try:
        reader = csv.DictReader(io.StringIO(decoded, newline=""), strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or not set(("name", "asset_tag", "category")).issubset(headers):
            raise HTTPException(
                status_code=422,
                detail="CSV must contain unique headers including name, asset_tag and category",
            )
        unknown = set(headers) - set(ASSET_CSV_COLUMNS)
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unsupported CSV columns: {', '.join(sorted(unknown))}")
        rows = list(reader)
    except csv.Error:
        raise HTTPException(status_code=400, detail="Malformed CSV file")
    if len(rows) > MAX_ASSET_IMPORT_ROWS:
        raise HTTPException(status_code=413, detail="CSV exceeds the 5,000 row limit")

    # SQLite's legacy transaction mode may defer the actual database
    # transaction until the first write. A SAVEPOINT created before that
    # would become the outermost transaction and releasing it could commit
    # each imported row before the batch audit is written. Start the SQLite
    # transaction explicitly so row savepoints remain inside the batch.
    connection = session.connection()
    if session.get_bind().dialect.name == "sqlite":
        driver_connection = connection.connection.driver_connection
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")

    created = 0
    skipped = 0
    errors = []
    for line_number, row in enumerate(rows, start=2):
        if None in row:
            skipped += 1
            errors.append({"row": line_number, "error": "Unexpected extra CSV fields"})
            continue
        values = {key: (value.strip() if value is not None else None) for key, value in row.items()}
        try:
            payload = AssetCreate.model_validate(values)
        except Exception as exc:
            skipped += 1
            errors.append({"row": line_number, "error": str(exc)[:300]})
            continue
        duplicate = session.scalar(select(Asset.id).where(
            Asset.organization_id == user.organization_id,
            (Asset.asset_tag == payload.asset_tag) |
            ((Asset.serial_number == payload.serial_number) if payload.serial_number else False),
        ))
        if duplicate is not None:
            skipped += 1
            errors.append({"row": line_number, "error": "Duplicate asset tag or serial number"})
            continue
        try:
            with session.begin_nested():
                asset = Asset(organization_id=user.organization_id, **payload.model_dump())
                session.add(asset)
                session.flush()
                record_audit(
                    session, user, "asset.imported", "asset", asset.id,
                    {"asset_tag": asset.asset_tag, "name": asset.name},
                )
            created += 1
        except IntegrityError:
            skipped += 1
            errors.append({"row": line_number, "error": "Duplicate asset tag or serial number"})

    try:
        record_audit(
            session, user, "asset.imported", "asset_import", "batch",
            {"created": created, "skipped": skipped, "error_count": len(errors)},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Asset import conflicts with current organization records",
        )
    return {"created": created, "skipped": skipped, "errors": errors}


@app.get("/api/v1/assets/{asset_id}", response_model=AssetRead, tags=["assets"])
def get_asset(asset_id: int, session: SessionDep, user: CurrentUser):
    asset = session.scalar(select(Asset).where(
        Asset.id == asset_id, Asset.organization_id == user.organization_id
    ))
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@app.patch("/api/v1/assets/{asset_id}", response_model=AssetRead, tags=["assets"])
def update_asset(
    asset_id: int,
    payload: AssetUpdate,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    asset = session.scalar(select(Asset).where(
        Asset.id == asset_id, Asset.organization_id == user.organization_id
    ))
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if payload.expected_version != asset.version:
        raise HTTPException(status_code=409, detail="Asset has changed. Reload the asset before saving.")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for required_field in ("name", "category"):
        if required_field in changes and changes[required_field] is None:
            raise HTTPException(status_code=422, detail=f"{required_field} cannot be null")
    before_after: dict[str, dict[str, str | None]] = {}
    for field, value in changes.items():
        old_value = getattr(asset, field)
        old_value = old_value.value if isinstance(old_value, Enum) else old_value
        new_value = value.value if isinstance(value, Enum) else value
        if old_value != new_value:
            before_after[field] = {"old": old_value, "new": new_value}
        setattr(asset, field, value)
    if not before_after:
        return asset
    try:
        session.flush()
        record_audit(
            session, user, "asset.updated", "asset", asset.id,
            {"changes": before_after},
        )
        session.commit()
    except (IntegrityError, StaleDataError):
        session.rollback()
        raise HTTPException(status_code=409, detail="Asset update conflicts with existing organization records or stale version")
    session.refresh(asset)
    return asset


@app.get("/api/v1/assets/{asset_id}/history", tags=["assets"])
def asset_history(
    asset_id: int,
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    asset = session.scalar(select(Asset.id).where(
        Asset.id == asset_id,
        Asset.organization_id == user.organization_id,
    ))
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    events = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == user.organization_id,
            AuditEvent.entity_type == "asset",
            AuditEvent.entity_id == str(asset_id),
        )
        .order_by(AuditEvent.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": event.id,
            "actor_user_id": event.actor_user_id,
            "action": event.action,
            "details": event.details,
            "created_at": event.created_at,
        }
        for event in events
    ]


@app.delete("/api/v1/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["assets"])
def delete_asset(
    asset_id: int,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    asset = session.scalar(select(Asset).where(
        Asset.id == asset_id, Asset.organization_id == user.organization_id
    ))
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    # Enforce the reference rule explicitly; SQLite test databases may not
    # enable foreign-key enforcement, unlike the production PostgreSQL setup.
    has_maintenance = session.scalar(
        select(MaintenanceRequest.id).where(
            MaintenanceRequest.organization_id == user.organization_id,
            MaintenanceRequest.asset_id == asset.id,
        ).limit(1)
    )
    if has_maintenance is not None:
        raise HTTPException(
            status_code=409,
            detail="Asset is referenced by maintenance requests and cannot be deleted",
        )
    try:
        record_audit(session, user, "asset.deleted", "asset", asset.id, {
            "asset_tag": asset.asset_tag, "name": asset.name, "category": asset.category,
            "status": asset.status.value,
        })
        session.delete(asset)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Asset deletion conflicts with current organization records",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/v1/departments",
    response_model=DepartmentRead,
    status_code=status.HTTP_201_CREATED,
    tags=["departments"],
)
def create_department(
    payload: DepartmentCreate,
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(UserRole.admin))],
):
    values = {
        "organization_id": admin.organization_id,
        "name": payload.name.strip(),
        "code": payload.code.strip().upper(),
    }
    if not values["name"] or not values["code"]:
        raise HTTPException(status_code=422, detail="Department name and code cannot be blank")
    department = Department(**values)
    session.add(department)
    try:
        session.flush()
        record_audit(session, admin, "department.created", "department", department.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Department creation conflicts with current organization records")
    session.refresh(department)
    return department


@app.get("/api/v1/departments", response_model=list[DepartmentRead], tags=["departments"])
def list_departments(
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    return list(session.scalars(
        select(Department).where(Department.organization_id == user.organization_id).order_by(Department.name)
    ).all())


@app.patch("/api/v1/departments/{department_id}", response_model=DepartmentRead, tags=["departments"])
def update_department(
    department_id: int,
    payload: DepartmentUpdate,
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(UserRole.admin))],
):
    department = session.scalar(select(Department).where(
        Department.id == department_id,
        Department.organization_id == admin.organization_id,
    ))
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")
    if payload.expected_version != department.version:
        raise HTTPException(status_code=409, detail="Department has changed; reload it before updating")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for field in ("name", "code"):
        if field in changes:
            if changes[field] is None or not changes[field].strip():
                raise HTTPException(status_code=422, detail=f"{field} cannot be blank")
            changes[field] = changes[field].strip()
    if "code" in changes:
        changes["code"] = changes["code"].upper()
    changes = {
        field: value for field, value in changes.items()
        if getattr(department, field) != value
    }
    if changes:
        for field, value in changes.items():
            setattr(department, field, value)
        try:
            session.flush()
            record_audit(session, admin, "department.updated", "department", department.id)
            session.commit()
        except (IntegrityError, StaleDataError):
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Department update conflicts with current organization records or stale version",
            )
    session.refresh(department)
    return department


@app.delete("/api/v1/departments/{department_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["departments"])
def delete_department(
    department_id: int,
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(UserRole.admin))],
):
    department = session.scalar(select(Department).where(
        Department.id == department_id,
        Department.organization_id == admin.organization_id,
    ))
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")
    assigned_employee_id = session.scalar(
        select(Employee.id).where(
            Employee.organization_id == admin.organization_id,
            Employee.department_id == department.id,
        ).limit(1)
    )
    if assigned_employee_id is not None:
        raise HTTPException(status_code=409, detail="Department is assigned to employees")
    try:
        record_audit(
            session, admin, "department.deleted", "department", department.id,
            {"name": department.name, "code": department.code},
        )
        session.delete(department)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Department deletion conflicts with current organization records",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/v1/employees",
    response_model=EmployeeRead,
    status_code=status.HTTP_201_CREATED,
    tags=["employees"],
)
def create_employee(
    payload: EmployeeCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    employee_number = payload.employee_number.strip()
    full_name = payload.full_name.strip()
    if not employee_number or not full_name:
        raise HTTPException(status_code=422, detail="Employee number and full name cannot be blank")
    if payload.department_id is not None and session.scalar(select(Department).where(
        Department.id == payload.department_id,
        Department.organization_id == user.organization_id,
    )) is None:
        raise HTTPException(status_code=422, detail="Department must belong to this organization")
    employee = Employee(
        organization_id=user.organization_id,
        department_id=payload.department_id,
        employee_number=employee_number,
        full_name=full_name,
        email=str(payload.email).lower() if payload.email else None,
        job_title=payload.job_title,
        phone=payload.phone,
    )
    session.add(employee)
    try:
        session.flush()
        record_audit(session, user, "employee.created", "employee", employee.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Employee creation conflicts with current organization records")
    session.refresh(employee)
    return employee


@app.get("/api/v1/employees", response_model=list[EmployeeRead], tags=["employees"])
def list_employees(
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
    search: Annotated[str | None, Query(max_length=120)] = None,
):
    statement = select(Employee).where(Employee.organization_id == user.organization_id)
    if search:
        term = f"%{search}%"
        statement = statement.where(
            Employee.full_name.ilike(term) | Employee.employee_number.ilike(term)
        )
    return list(session.scalars(statement.order_by(Employee.full_name).limit(500)).all())


@app.patch("/api/v1/employees/{employee_id}", response_model=EmployeeRead, tags=["employees"])
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    employee = session.scalar(select(Employee).where(
        Employee.id == employee_id,
        Employee.organization_id == user.organization_id,
    ))
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    if payload.expected_version != employee.version:
        raise HTTPException(status_code=409, detail="Employee has changed; reload it before updating")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for field in ("employee_number", "full_name", "is_active"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    for field in ("employee_number", "full_name"):
        if field in changes:
            changes[field] = changes[field].strip()
            if not changes[field]:
                raise HTTPException(status_code=422, detail=f"{field} cannot be blank")
    if "email" in changes and changes["email"] is not None:
        changes["email"] = str(changes["email"]).lower()
    if changes.get("department_id") is not None and session.scalar(select(Department).where(
        Department.id == changes["department_id"],
        Department.organization_id == user.organization_id,
    )) is None:
        raise HTTPException(status_code=422, detail="Department must belong to this organization")
    changes = {
        field: value for field, value in changes.items()
        if getattr(employee, field) != value
    }
    if changes:
        for field, value in changes.items():
            setattr(employee, field, value)
        try:
            session.flush()
            record_audit(session, user, "employee.updated", "employee", employee.id, {"fields": sorted(changes)})
            session.commit()
        except (IntegrityError, StaleDataError):
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Employee update conflicts with current organization records or stale version",
            )
    session.refresh(employee)
    return employee


@app.delete("/api/v1/employees/{employee_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["employees"])
def delete_employee(
    employee_id: int,
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(UserRole.admin))],
):
    employee = session.scalar(select(Employee).where(
        Employee.id == employee_id,
        Employee.organization_id == admin.organization_id,
    ))
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        record_audit(
            session, admin, "employee.deleted", "employee", employee.id,
            {
                "employee_number": employee.employee_number,
                "full_name": employee.full_name,
                "department_id": employee.department_id,
                "job_title": employee.job_title,
                "is_active": employee.is_active,
            },
        )
        session.delete(employee)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Employee deletion conflicts with current organization records",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/v1/maintenance-requests",
    response_model=MaintenanceRequestRead,
    status_code=status.HTTP_201_CREATED,
    tags=["maintenance"],
)
def create_maintenance_request(
    payload: MaintenanceRequestCreate,
    session: SessionDep,
    user: CurrentUser,
):
    asset = session.scalar(select(Asset).where(
        Asset.id == payload.asset_id,
        Asset.organization_id == user.organization_id,
    ))
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    title = payload.title.strip()
    description = payload.description.strip()
    if not title or not description:
        raise HTTPException(status_code=422, detail="Title and description cannot be blank")
    request_record = MaintenanceRequest(
        organization_id=user.organization_id,
        asset_id=asset.id,
        requested_by_user_id=user.id,
        title=title,
        description=description,
        priority=payload.priority,
    )
    session.add(request_record)
    try:
        session.flush()
        record_audit(
            session, user, "maintenance_request.created", "maintenance_request",
            request_record.id, {"asset_id": asset.id, "priority": request_record.priority.value},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Maintenance request conflicts with current asset or user records",
        )
    session.refresh(request_record)
    return request_record


@app.get(
    "/api/v1/maintenance-requests",
    response_model=list[MaintenanceRequestRead],
    tags=["maintenance"],
)
def list_maintenance_requests(
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    statement = select(MaintenanceRequest).where(
        MaintenanceRequest.organization_id == user.organization_id
    )
    if user.role == UserRole.viewer:
        statement = statement.where(MaintenanceRequest.requested_by_user_id == user.id)
    statement = statement.order_by(MaintenanceRequest.id.desc()).limit(limit)
    return list(session.scalars(statement).all())


@app.get(
    "/api/v1/maintenance-requests/{request_id}",
    response_model=MaintenanceRequestRead,
    tags=["maintenance"],
)
def get_maintenance_request(
    request_id: int,
    session: SessionDep,
    user: CurrentUser,
):
    statement = select(MaintenanceRequest).where(
        MaintenanceRequest.id == request_id,
        MaintenanceRequest.organization_id == user.organization_id,
    )
    if user.role == UserRole.viewer:
        statement = statement.where(MaintenanceRequest.requested_by_user_id == user.id)
    request_record = session.scalar(statement)
    if request_record is None:
        raise HTTPException(status_code=404, detail="Maintenance request not found")
    return request_record


@app.patch(
    "/api/v1/maintenance-requests/{request_id}",
    response_model=MaintenanceRequestRead,
    tags=["maintenance"],
)
def update_maintenance_request(
    request_id: int,
    payload: MaintenanceRequestUpdate,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    request_record = session.scalar(select(MaintenanceRequest).where(
        MaintenanceRequest.id == request_id,
        MaintenanceRequest.organization_id == user.organization_id,
    ))
    if request_record is None:
        raise HTTPException(status_code=404, detail="Maintenance request not found")
    if payload.expected_version != request_record.version:
        raise HTTPException(status_code=409, detail="Maintenance request has changed; reload it before updating")
    changes = payload.model_dump(exclude={"expected_version"}, exclude_unset=True)
    for required_field in ("title", "description", "priority", "status"):
        if required_field in changes and changes[required_field] is None:
            raise HTTPException(status_code=422, detail=f"{required_field} cannot be null")
    if "title" in changes:
        changes["title"] = changes["title"].strip()
        if not changes["title"]:
            raise HTTPException(status_code=422, detail="title cannot be blank")
    if "description" in changes:
        changes["description"] = changes["description"].strip()
        if not changes["description"]:
            raise HTTPException(status_code=422, detail="description cannot be blank")
    if changes.get("assigned_to_user_id") is not None:
        assignee = session.scalar(select(User).where(
            User.id == changes["assigned_to_user_id"],
            User.organization_id == user.organization_id,
            User.is_active.is_(True),
        ))
        if assignee is None:
            raise HTTPException(status_code=422, detail="Assignee must be an active user in this organization")
    resolved_at = request_record.resolved_at
    if changes.get("status") == MaintenanceStatus.resolved and resolved_at is None:
        resolved_at = datetime.now(timezone.utc)
    elif changes.get("status") in (MaintenanceStatus.open, MaintenanceStatus.in_progress):
        resolved_at = None
    resolved_at_changed = resolved_at != request_record.resolved_at
    changes = {
        field: value for field, value in changes.items()
        if getattr(request_record, field) != value
    }
    audit_fields = set(changes)
    audit_changes = {}
    for field, new_value in changes.items():
        # Free-text maintenance content can contain personal or confidential data.
        # Record only that it changed; retain before/after values for controlled fields.
        if field in {"priority", "status", "assigned_to_user_id"}:
            old_value = getattr(request_record, field)
            audit_changes[field] = {
                "old": old_value.value if isinstance(old_value, Enum) else old_value,
                "new": new_value.value if isinstance(new_value, Enum) else new_value,
            }
    if resolved_at_changed:
        audit_fields.add("resolved_at")
        audit_changes["resolved_at"] = {
            "old": request_record.resolved_at.isoformat() if request_record.resolved_at else None,
            "new": resolved_at.isoformat() if resolved_at else None,
        }
        request_record.resolved_at = resolved_at
    if changes or resolved_at_changed:
        try:
            for field, value in changes.items():
                setattr(request_record, field, value)
            record_audit(
                session, user, "maintenance_request.updated", "maintenance_request",
                request_record.id, {
                    "fields": sorted(audit_fields),
                    "changes": audit_changes,
                },
            )
            session.commit()
        except (IntegrityError, StaleDataError):
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Maintenance request update conflicts with current organization records or stale version",
            )
    session.refresh(request_record)
    return request_record


@app.post(
    "/api/v1/software-licenses",
    response_model=SoftwareLicenseRead,
    status_code=status.HTTP_201_CREATED,
    tags=["software"],
)
def create_software_license(
    payload: SoftwareLicenseCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    values = payload.model_dump()
    values["name"] = values["name"].strip()
    if not values["name"]:
        raise HTTPException(status_code=422, detail="name cannot be blank")
    if values["license_reference"] is not None:
        values["license_reference"] = values["license_reference"].strip() or None
    license_record = SoftwareLicense(organization_id=user.organization_id, **values)
    session.add(license_record)
    try:
        session.flush()
        record_audit(
            session, user, "software_license.created", "software_license", license_record.id,
            {"name": license_record.name, "license_type": license_record.license_type.value},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Software license creation conflicts with current organization records",
        )
    session.refresh(license_record)
    return license_record


@app.get("/api/v1/software-licenses", response_model=list[SoftwareLicenseRead], tags=["software"])
def list_software_licenses(
    session: SessionDep,
    user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    search: Annotated[str | None, Query(max_length=120)] = None,
):
    statement = select(SoftwareLicense).where(
        SoftwareLicense.organization_id == user.organization_id
    )
    if search:
        statement = statement.where(SoftwareLicense.name.ilike(f"%{search}%"))
    statement = statement.order_by(SoftwareLicense.id.desc()).offset(offset).limit(limit)
    return list(session.scalars(statement).all())


@app.get(
    "/api/v1/software-licenses/{license_id}",
    response_model=SoftwareLicenseRead,
    tags=["software"],
)
def get_software_license(license_id: int, session: SessionDep, user: CurrentUser):
    license_record = session.scalar(select(SoftwareLicense).where(
        SoftwareLicense.id == license_id,
        SoftwareLicense.organization_id == user.organization_id,
    ))
    if license_record is None:
        raise HTTPException(status_code=404, detail="Software license not found")
    return license_record


@app.patch(
    "/api/v1/software-licenses/{license_id}",
    response_model=SoftwareLicenseRead,
    tags=["software"],
)
def update_software_license(
    license_id: int,
    payload: SoftwareLicenseUpdate,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    license_record = session.scalar(select(SoftwareLicense).where(
        SoftwareLicense.id == license_id,
        SoftwareLicense.organization_id == user.organization_id,
    ))
    if license_record is None:
        raise HTTPException(status_code=404, detail="Software license not found")
    if payload.expected_version != license_record.record_version:
        raise HTTPException(status_code=409, detail="Software license has changed. Reload the license before saving.")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for required_field in ("name", "license_type", "seats_total", "seats_used", "status"):
        if required_field in changes and changes[required_field] is None:
            raise HTTPException(status_code=422, detail=f"{required_field} cannot be null")
    total = changes.get("seats_total", license_record.seats_total)
    used = changes.get("seats_used", license_record.seats_used)
    if used > total:
        raise HTTPException(status_code=422, detail="seats_used cannot exceed seats_total")
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if not changes["name"]:
            raise HTTPException(status_code=422, detail="name cannot be blank")
    if "license_reference" in changes and changes["license_reference"] is not None:
        changes["license_reference"] = changes["license_reference"].strip() or None
    purchased_at = changes.get("purchased_at", license_record.purchased_at)
    expires_at = changes.get("expires_at", license_record.expires_at)
    if purchased_at and expires_at and expires_at < purchased_at:
        raise HTTPException(status_code=422, detail="expires_at cannot be earlier than purchased_at")
    changes = {
        field: value for field, value in changes.items()
        if getattr(license_record, field) != value
    }
    if changes:
        for field, value in changes.items():
            setattr(license_record, field, value)
        try:
            session.flush()
            record_audit(
                session, user, "software_license.updated", "software_license", license_record.id,
                {"fields": sorted(changes)},
            )
            session.commit()
        except (IntegrityError, StaleDataError):
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Software license update conflicts with current organization records or stale version",
            )
    session.refresh(license_record)
    return license_record


@app.delete(
    "/api/v1/software-licenses/{license_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["software"],
)
def delete_software_license(
    license_id: int,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(UserRole.admin, UserRole.editor))],
):
    license_record = session.scalar(select(SoftwareLicense).where(
        SoftwareLicense.id == license_id,
        SoftwareLicense.organization_id == user.organization_id,
    ))
    if license_record is None:
        raise HTTPException(status_code=404, detail="Software license not found")
    try:
        record_audit(
            session, user, "software_license.deleted", "software_license", license_record.id,
            {
                "name": license_record.name,
                "publisher": license_record.publisher,
                "version": license_record.version,
                "license_type": license_record.license_type.value,
                "license_reference": license_record.license_reference,
                "seats_total": license_record.seats_total,
                "seats_used": license_record.seats_used,
                "purchased_at": license_record.purchased_at.isoformat() if license_record.purchased_at else None,
                "expires_at": license_record.expires_at.isoformat() if license_record.expires_at else None,
                "status": license_record.status.value,
            },
        )
        session.delete(license_record)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Software license deletion conflicts with current organization records",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/v1/audit-events", tags=["audit"])
def list_audit_events(
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(UserRole.admin))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == admin.organization_id)
        .order_by(AuditEvent.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": event.id,
            "actor_user_id": event.actor_user_id,
            "action": event.action,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "details": event.details,
            "created_at": event.created_at,
        }
        for event in events
    ]
