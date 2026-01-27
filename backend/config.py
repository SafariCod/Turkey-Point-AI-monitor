from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Dict, Set


@dataclass(frozen=True)
class SecurityConfig:
    hmac_secrets: Dict[str, str]
    sig_window_sec: int = 300
    nonce_ttl_sec: int = 600


@dataclass(frozen=True)
class NodeBehaviorConfig:
    disable_pm25_nodes: Set[str]
    pm25_fallback: float = 1.2
    pm25_flag_name: str = "pm25_forced_normal"


@dataclass(frozen=True)
class StatusConfig:
    recompute_interval_sec: int = 30
    hysteresis_sec: int = 60


@dataclass(frozen=True)
class AppConfig:
    security: SecurityConfig
    behavior: NodeBehaviorConfig
    status: StatusConfig


def _parse_hmac_secrets(raw: str) -> Dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    # allow simple "node=secret,node2=secret2" form
    result: Dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k and v:
                result[k] = v
    return result


def load_config() -> AppConfig:
    hmac_raw = os.getenv("TELEMETRY_HMAC_SECRETS", "")
    sig_window = int(os.getenv("TELEMETRY_SIG_WINDOW_SEC", "300"))
    nonce_ttl = int(os.getenv("TELEMETRY_NONCE_TTL_SEC", "600"))
    disable_pm25 = {s.strip() for s in os.getenv("DISABLE_PM25_NODES", "").split(",") if s.strip()}
    pm25_fallback = float(os.getenv("PM25_FALLBACK", "1.2"))
    pm25_flag = os.getenv("PM25_FLAG_NAME", "pm25_forced_normal")
    recompute_interval = int(os.getenv("STATUS_RECOMPUTE_SEC", "30"))
    hysteresis = int(os.getenv("STATUS_HYSTERESIS_SEC", "60"))

    return AppConfig(
        security=SecurityConfig(
            hmac_secrets=_parse_hmac_secrets(hmac_raw),
            sig_window_sec=sig_window,
            nonce_ttl_sec=nonce_ttl,
        ),
        behavior=NodeBehaviorConfig(
            disable_pm25_nodes=disable_pm25,
            pm25_fallback=pm25_fallback,
            pm25_flag_name=pm25_flag,
        ),
        status=StatusConfig(
            recompute_interval_sec=recompute_interval,
            hysteresis_sec=hysteresis,
        ),
    )
