"""JWT sign/verify and require_user dependency tests."""

import time

import jwt
import pytest
from fastapi import HTTPException

from app.security import require_user, sign_service_token, verify_service_token
from app.settings import settings


def test_sign_and_verify_roundtrip():
    tok = sign_service_token("user-123", "u@example.com", ttl_seconds=60)
    payload = verify_service_token(tok)
    assert payload["userId"] == "user-123"
    assert payload["email"] == "u@example.com"
    assert payload["exp"] > payload["iat"]


def test_verify_rejects_bad_signature():
    tok = jwt.encode({"userId": "x", "exp": int(time.time()) + 60}, "wrong-secret", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        verify_service_token(tok)
    assert ei.value.status_code == 401


def test_verify_rejects_expired():
    expired = jwt.encode(
        {"userId": "x", "iat": int(time.time()) - 200, "exp": int(time.time()) - 100},
        settings.JWT_SHARED_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as ei:
        verify_service_token(expired)
    assert ei.value.status_code == 401


def test_require_user_missing_header_rejects():
    with pytest.raises(HTTPException) as ei:
        require_user(authorization=None)
    assert ei.value.status_code == 401


def test_require_user_wrong_scheme_rejects():
    with pytest.raises(HTTPException):
        require_user(authorization="Basic foo")


def test_require_user_happy_path():
    tok = sign_service_token("user-abc", "a@b.com")
    claims = require_user(authorization=f"Bearer {tok}")
    assert claims["userId"] == "user-abc"
