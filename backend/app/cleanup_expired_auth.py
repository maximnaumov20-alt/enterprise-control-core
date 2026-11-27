"""Remove expired, one-time authentication artifacts.

Run periodically with: python -m app.cleanup_expired_auth
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.main import SessionLocal
from app.mfa_models import MFAChallenge, MFAFactor


def cleanup_expired_auth_records(
    session: Session, *, now: datetime | None = None
) -> dict[str, int]:
    """Delete expired MFA challenges and pending enrollments, never enabled factors."""
    cutoff = now or datetime.now(timezone.utc)
    challenges = session.execute(
        delete(MFAChallenge).where(MFAChallenge.expires_at <= cutoff)
    )
    pending_factors = session.execute(
        delete(MFAFactor).where(
            MFAFactor.status == "pending",
            MFAFactor.pending_expires_at.is_not(None),
            MFAFactor.pending_expires_at <= cutoff,
        )
    )
    return {
        "expired_challenges": max(challenges.rowcount or 0, 0),
        "expired_pending_enrollments": max(pending_factors.rowcount or 0, 0),
    }


def main() -> int:
    with SessionLocal.begin() as session:
        counts = cleanup_expired_auth_records(session)
    print(
        "Expired authentication artifacts removed: "
        f"{counts['expired_challenges']} challenge(s), "
        f"{counts['expired_pending_enrollments']} pending enrollment(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
