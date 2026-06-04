from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_callback_token() -> str:
    return secrets.token_urlsafe(32)


def hash_callback_token(cloud_run_id: str, worker_id: str, token: str) -> str:
    payload = f"{cloud_run_id}:{worker_id}:{token}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_callback_token(
    cloud_run_id: str,
    worker_id: str,
    token: str,
    expected_hash: str,
) -> bool:
    actual_hash = hash_callback_token(cloud_run_id, worker_id, token)
    return hmac.compare_digest(actual_hash, expected_hash)
