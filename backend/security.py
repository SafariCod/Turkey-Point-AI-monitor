from __future__ import annotations

import hmac
import hashlib
import time
from dataclasses import dataclass
from typing import Dict, Tuple

from config import SecurityConfig


@dataclass
class SignatureResult:
    ok: bool
    error: str | None = None
    node_id: str | None = None
    timestamp: int | None = None
    nonce: str | None = None


class NonceCache:
    def __init__(self, ttl_sec: int) -> None:
        self._ttl = ttl_sec
        self._store: Dict[str, Dict[str, int]] = {}

    def seen(self, node_id: str, nonce: str, now: int) -> bool:
        node_nonces = self._store.setdefault(node_id, {})
        # purge old
        expired = [n for n, ts in node_nonces.items() if now - ts > self._ttl]
        for n in expired:
            node_nonces.pop(n, None)
        if nonce in node_nonces:
            return True
        node_nonces[nonce] = now
        return False


def _sign_message(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_signature(
    headers: Dict[str, str],
    body_bytes: bytes,
    config: SecurityConfig,
    nonce_cache: NonceCache,
) -> SignatureResult:
    node_id = headers.get("X-Node-Id", "")
    ts_raw = headers.get("X-Timestamp", "")
    nonce = headers.get("X-Nonce", "")
    sig = headers.get("X-Signature", "")

    if not node_id or not ts_raw or not nonce or not sig:
        return SignatureResult(ok=False, error="Missing signature headers")

    try:
        ts = int(ts_raw)
    except ValueError:
        return SignatureResult(ok=False, error="Invalid timestamp header")

    now = int(time.time())
    if abs(now - ts) > config.sig_window_sec:
        return SignatureResult(ok=False, error="Timestamp outside allowed window")

    if nonce_cache.seen(node_id, nonce, now):
        return SignatureResult(ok=False, error="Nonce replay detected")

    secret = config.hmac_secrets.get(node_id)
    if not secret:
        return SignatureResult(ok=False, error="Unknown node_id for HMAC")

    message = b".".join(
        [
            node_id.encode("utf-8"),
            str(ts).encode("utf-8"),
            nonce.encode("utf-8"),
            body_bytes,
        ]
    )
    expected = _sign_message(secret, message)
    if not hmac.compare_digest(expected, sig):
        return SignatureResult(ok=False, error="Invalid signature")

    return SignatureResult(ok=True, node_id=node_id, timestamp=ts, nonce=nonce)
