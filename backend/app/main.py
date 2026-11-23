from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/ecc.db")
if DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class AssetStatus(str, Enum):
    active = "active"
    in_repair = "in_repair"
    retired = "retired"


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    asset_tag: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
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


def get_session():
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


app = FastAPI(
    title="Enterprise Control Center API",
    version="0.1.0",
    description="Early-development API for enterprise IT asset inventory.",
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


@app.post(
    "/api/v1/assets",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
def create_asset(payload: AssetCreate, session: SessionDep):
    existing = session.scalar(select(Asset).where(Asset.asset_tag == payload.asset_tag))
    if existing:
        raise HTTPException(status_code=409, detail="Asset tag already exists")

    if payload.serial_number:
        duplicate_serial = session.scalar(
            select(Asset).where(Asset.serial_number == payload.serial_number)
        )
        if duplicate_serial:
            raise HTTPException(status_code=409, detail="Serial number already exists")

    asset = Asset(**payload.model_dump())
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


@app.get("/api/v1/assets", response_model=list[AssetRead], tags=["assets"])
def list_assets(
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    search: Annotated[str | None, Query(max_length=120)] = None,
):
    statement = select(Asset).order_by(Asset.id.desc()).offset(offset).limit(limit)
    if search:
        statement = statement.where(Asset.name.ilike(f"%{search}%"))
    return list(session.scalars(statement).all())


@app.get("/api/v1/assets/{asset_id}", response_model=AssetRead, tags=["assets"])
def get_asset(asset_id: int, session: SessionDep):
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@app.patch("/api/v1/assets/{asset_id}", response_model=AssetRead, tags=["assets"])
def update_asset(asset_id: int, payload: AssetUpdate, session: SessionDep):
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    changes = payload.model_dump(exclude_unset=True)
    for required_field in ("name", "category"):
        if required_field in changes and changes[required_field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{required_field} cannot be null",
            )

    if changes.get("serial_number"):
        duplicate = session.scalar(
            select(Asset).where(
                Asset.serial_number == changes["serial_number"],
                Asset.id != asset_id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Serial number already exists")

    for field, value in changes.items():
        setattr(asset, field, value)

    session.commit()
    session.refresh(asset)
    return asset


@app.delete("/api/v1/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["assets"])
def delete_asset(asset_id: int, session: SessionDep):
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    session.delete(asset)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
