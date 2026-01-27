from __future__ import annotations

from typing import Dict, List, Tuple
from datetime import datetime

from config import AppConfig


def normalize_reading(
    node_id: str,
    payload: Dict,
    ts_iso: str,
    fields: List[str],
    config: AppConfig,
) -> Tuple[Dict, Dict[str, bool]]:
    reading: Dict = {"node_id": node_id, "ts": ts_iso}
    for field in fields:
        val = payload.get(field)
        if val is None:
            reading[field] = None
        else:
            reading[field] = float(val)
    flags: Dict[str, bool] = {}
    if node_id in config.behavior.disable_pm25_nodes:
        reading["pm25"] = config.behavior.pm25_fallback
        flags[config.behavior.pm25_flag_name] = True
    return reading, flags
