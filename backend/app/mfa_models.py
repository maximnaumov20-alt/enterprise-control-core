"""Database models for the MFA credential lifecycle."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, BigInteger, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.main import Base


class MFAFactor(Base):
    __tablename__ = "mfa_factors"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_mfa_factors_org_user"),
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            ondelete="CASCADE",
            name="fk_mfa_factors_user_same_org",
        ),
        CheckConstraint("status IN ('pending', 'enabled', 'disabled')", name="ck_mfa_factors_status"),
        CheckConstraint("last_used_counter IS NULL OR last_used_counter >= 0", name="ck_mfa_factors_counter"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    encrypted_secret: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    pending_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MFARecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            ondelete="CASCADE",
            name="fk_mfa_recovery_codes_user_same_org",
        ),
        UniqueConstraint("organization_id", "user_id", "code_hash", name="uq_mfa_recovery_codes_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MFAChallenge(Base):
    __tablename__ = "mfa_challenges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            ondelete="CASCADE",
            name="fk_mfa_challenges_user_same_org",
        ),
        UniqueConstraint("token_hash", name="uq_mfa_challenges_token_hash"),
        CheckConstraint("attempts >= 0 AND attempts <= 5", name="ck_mfa_challenges_attempts"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
