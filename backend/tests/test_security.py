import time

from config import SecurityConfig
from security import NonceCache, verify_signature


def _sign(secret: str, node_id: str, ts: int, nonce: str, body: bytes) -> str:
    import hmac
    import hashlib
    msg = b".".join([node_id.encode(), str(ts).encode(), nonce.encode(), body])
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def test_signature_validation_ok():
    node_id = "ground_1"
    secret = "s3cr3t"
    ts = int(time.time())
    nonce = "abc123"
    body = b'{"device_id":"ground_1","timestamp":1,"data":{"pm25":1}}'
    sig = _sign(secret, node_id, ts, nonce, body)
    headers = {
        "X-Node-Id": node_id,
        "X-Timestamp": str(ts),
        "X-Nonce": nonce,
        "X-Signature": sig,
    }
    cfg = SecurityConfig(hmac_secrets={node_id: secret}, sig_window_sec=300, nonce_ttl_sec=600)
    cache = NonceCache(cfg.nonce_ttl_sec)
    res = verify_signature(headers, body, cfg, cache)
    assert res.ok


def test_nonce_replay_detected():
    node_id = "ground_1"
    secret = "s3cr3t"
    ts = int(time.time())
    nonce = "dupnonce"
    body = b'{"device_id":"ground_1","timestamp":1,"data":{"pm25":1}}'
    sig = _sign(secret, node_id, ts, nonce, body)
    headers = {
        "X-Node-Id": node_id,
        "X-Timestamp": str(ts),
        "X-Nonce": nonce,
        "X-Signature": sig,
    }
    cfg = SecurityConfig(hmac_secrets={node_id: secret}, sig_window_sec=300, nonce_ttl_sec=600)
    cache = NonceCache(cfg.nonce_ttl_sec)
    res1 = verify_signature(headers, body, cfg, cache)
    res2 = verify_signature(headers, body, cfg, cache)
    assert res1.ok
    assert not res2.ok
