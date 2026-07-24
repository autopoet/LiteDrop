from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

PBKDF2_MIN_ITERATIONS = 100_000
PBKDF2_ITERATIONS = 310_000
PBKDF2_MAX_ITERATIONS = 1_000_000


def _decode_password_hash(
    encoded: str,
) -> tuple[int, bytes, bytes] | None:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return None
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text, altchars=b"-_", validate=True)
        digest = base64.b64decode(digest_text, altchars=b"-_", validate=True)
        return iterations, salt, digest
    except (ValueError, TypeError, binascii.Error):
        return None


def _is_valid_password_hash(
    decoded: tuple[int, bytes, bytes],
    min_iterations: int = PBKDF2_MIN_ITERATIONS,
) -> bool:
    iterations, salt, digest = decoded
    return (
        min_iterations <= iterations <= PBKDF2_MAX_ITERATIONS
        and len(salt) >= 16
        and len(digest) == hashlib.sha256().digest_size
    )


def is_password_hash(
    encoded: str,
    min_iterations: int = PBKDF2_MIN_ITERATIONS,
) -> bool:
    decoded = _decode_password_hash(encoded)
    return decoded is not None and _is_valid_password_hash(decoded, min_iterations)


def hash_password(password: str) -> str:
    """Hash a password with only Python's standard library."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PBKDF2_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(digest).decode(),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    decoded = _decode_password_hash(encoded)
    if decoded is None or not _is_valid_password_hash(decoded):
        return False
    iterations, salt, expected = decoded
    try:
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    except (OverflowError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def create_token(secret: str, token_type: str, subject: str, lifetime: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
        "nonce": secrets.token_urlsafe(8),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: str, token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValueError("令牌无效或已过期") from exc
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise ValueError("令牌类型不正确")
    return payload


def _main() -> None:
    parser = argparse.ArgumentParser(description="CodeDrop security helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("hash-password")
    command.add_argument("password")
    args = parser.parse_args()
    if args.command == "hash-password":
        print(hash_password(args.password))


if __name__ == "__main__":
    _main()
