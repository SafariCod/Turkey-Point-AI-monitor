import time

from config import AppConfig, SecurityConfig, NodeBehaviorConfig, StatusConfig
from status_engine import StatusEngine, NodeStatus


def test_hysteresis_blocks_flapping():
    cfg = AppConfig(
        security=SecurityConfig(hmac_secrets={}),
        behavior=NodeBehaviorConfig(disable_pm25_nodes=set()),
        status=StatusConfig(recompute_interval_sec=30, hysteresis_sec=60),
    )
    engine = StatusEngine(cfg)
    now = int(time.time())
    engine._cache.computed_at_epoch = now
    engine._cache.node_status["ground_1"] = NodeStatus(
        node_id="ground_1",
        status="Safe",
        confidence=0.4,
        reasons=["Within expected ranges"],
        summary="OK",
        computed_at="2026-01-01T00:00:00Z",
        latest={},
        flags={},
    )
    # new status would be Warning, but within hysteresis window
    status = engine._apply_hysteresis("ground_1", "Warning", now)
    assert status == "Safe"
