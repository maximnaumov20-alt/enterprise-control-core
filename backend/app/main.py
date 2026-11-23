from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Callable

import jwt
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pwdlib import PasswordHash
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import (
    CheckConstraint, Date, DateTime, Enum as SAEnum, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, Text, UniqueConstraint,
    create_engine, select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/ecc.db")
if DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
SECRET_KEY = os.getenv("ECC_SECRET_KEY")
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "20"))
if SECRET_KEY and len(SECRET_KEY.encode("utf-8")) < 32:
    raise RuntimeError("ECC_SECRET_KEY must contain at least 32 bytes.")
if not 5 <= ACCESS_TOKEN_MINUTES <= 60:
    raise RuntimeError("ACCESS_TOKEN_MINUTES must be between 5 and 60.")
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


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
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    manufacturer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    assigned_to: str | None = Field(default=None, max_length=160)
    status: AssetStatus | None = None


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


class MaintenanceRequestCreate(BaseModel):
    asset_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    priority: MaintenancePriority = MaintenancePriority.normal


class MaintenanceRequestUpdate(BaseModel):
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


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=128)
    role: UserRole


class UserUpdate(BaseModel):
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


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CurrentUserRead(UserRead):
    organization_name: str


def get_session():
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_access_token(user: User) -> str:
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
        "iss": "ecc-api",
        "aud": "ecc-client",
    }
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
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise unauthorized
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
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


app = FastAPI(
    title="Enterprise Control Center API",
    version="0.2.0",
    description="Early-development API for organization-scoped IT asset inventory.",
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


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "enterprise-control-center-api"}


@app.post("/api/v1/auth/token", response_model=TokenRead, tags=["authentication"])
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
):
    user = session.scalar(select(User).where(User.email == form.username.strip().lower()))
    if user is None or not user.is_active or not password_hash.verify(form.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenRead(
        access_token=create_access_token(user),
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
    user.password_hash = password_hash.hash(payload.new_password)
    record_audit(session, user, "user.password_changed", "user", user.id)
    session.commit()
    return {"status": "password_updated"}


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
    session.flush()
    record_audit(session, admin, "user.created", "user", user.id, {"role": user.role.value})
    session.commit()
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
    changes = payload.model_dump(exclude_unset=True)
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
    for field, value in changes.items():
        setattr(target, field, value)
    record_audit(session, admin, "user.updated", "user", target.id, {"fields": sorted(changes)})
    session.commit()
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
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Asset tag or serial number already exists")
    record_audit(session, user, "asset.created", "asset", asset.id, {"asset_tag": asset.asset_tag})
    session.commit()
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
    changes = payload.model_dump(exclude_unset=True)
    for required_field in ("name", "category"):
        if required_field in changes and changes[required_field] is None:
            raise HTTPException(status_code=422, detail=f"{required_field} cannot be null")
    for field, value in changes.items():
        setattr(asset, field, value)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Serial number already exists")
    record_audit(session, user, "asset.updated", "asset", asset.id, {"fields": sorted(changes)})
    session.commit()
    session.refresh(asset)
    return asset


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
    record_audit(session, user, "asset.deleted", "asset", asset.id, {"asset_tag": asset.asset_tag})
    session.delete(asset)
    session.commit()
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
    session.flush()
    record_audit(
        session, user, "maintenance_request.created", "maintenance_request",
        request_record.id, {"asset_id": asset.id, "priority": request_record.priority.value},
    )
    session.commit()
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
    changes = payload.model_dump(exclude_unset=True)
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
    if changes.get("status") == MaintenanceStatus.resolved:
        request_record.resolved_at = datetime.now(timezone.utc)
    elif changes.get("status") in (MaintenanceStatus.open, MaintenanceStatus.in_progress):
        request_record.resolved_at = None
    for field, value in changes.items():
        setattr(request_record, field, value)
    record_audit(
        session, user, "maintenance_request.updated", "maintenance_request",
        request_record.id, {"fields": sorted(changes)},
    )
    session.commit()
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
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="License reference already exists")
    record_audit(
        session, user, "software_license.created", "software_license", license_record.id,
        {"name": license_record.name, "license_type": license_record.license_type.value},
    )
    session.commit()
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
    changes = payload.model_dump(exclude_unset=True)
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
    for field, value in changes.items():
        setattr(license_record, field, value)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="License reference already exists")
    record_audit(
        session, user, "software_license.updated", "software_license", license_record.id,
        {"fields": sorted(changes)},
    )
    session.commit()
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
    record_audit(
        session, user, "software_license.deleted", "software_license", license_record.id,
        {"name": license_record.name},
    )
    session.delete(license_record)
    session.commit()
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
