from config import AppConfig, SecurityConfig, NodeBehaviorConfig, StatusConfig
from ingest_utils import normalize_reading


def test_pm25_disabled_for_ground_2():
    cfg = AppConfig(
        security=SecurityConfig(hmac_secrets={}),
        behavior=NodeBehaviorConfig(disable_pm25_nodes={"ground_2"}, pm25_fallback=1.2, pm25_flag_name="pm25_forced_normal"),
        status=StatusConfig(),
    )
    reading, flags = normalize_reading(
        "ground_2",
        {"pm25": 500.0, "radiation_cpm": 1.0},
        "2026-01-01T00:00:00Z",
        ["radiation_cpm", "pm25"],
        cfg,
    )
    assert reading["pm25"] == 1.2
    assert flags["pm25_forced_normal"] is True
