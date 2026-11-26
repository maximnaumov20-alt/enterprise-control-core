import pytest
from fastapi import HTTPException

from app import mfa_routes


def test_mfa_rate_limit_fails_closed_without_redis_by_default(monkeypatch):
    monkeypatch.setattr(mfa_routes, "redis_client", None)
    monkeypatch.setattr(mfa_routes, "MFA_ALLOW_LOCAL_RATELIMIT", False)

    with pytest.raises(HTTPException) as error:
        mfa_routes._enforce_mfa_rate_limit("test:key")

    assert error.value.status_code == 503


def test_mfa_rate_limit_requires_explicit_local_fallback_opt_in(monkeypatch):
    class LocalLimiter:
        def check(self, key):
            assert key == "test:key"
            return None

    monkeypatch.setattr(mfa_routes, "redis_client", None)
    monkeypatch.setattr(mfa_routes, "MFA_ALLOW_LOCAL_RATELIMIT", True)
    monkeypatch.setattr(mfa_routes, "mfa_rate_limiter", LocalLimiter())

    mfa_routes._enforce_mfa_rate_limit("test:key")
