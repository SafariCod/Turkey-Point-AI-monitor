import math
import joblib
import numpy as np
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).parent
GROUND_MODEL_PATH = HERE / "isoforest_ground.pkl"
WATER_MODEL_PATH = HERE / "isoforest_water.pkl"

# Feature sets
GROUND_FEATURES = ["radiation_cpm", "pm25", "air_temp_c", "humidity", "pressure_hpa", "voc"]
WATER_FEATURES = ["tds", "ph", "turbidity", "water_temp_c"]

# Hard thresholds (fail-safe): physics/engineering limits that immediately drive danger if exceeded.
HARD_THRESHOLDS = {
    "ground": {
        "radiation_cpm": {"warning_high": 120, "danger_high": 500, "extreme_high": 1000},
        "pm25": {"warning_high": 75, "danger_high": 150},
        "voc": {"warning_high": 800, "danger_high": 1500},
        "humidity": {"danger_low": 10, "danger_high": 95},
        "air_temp_c": {"danger_low": -10, "danger_high": 60},
        "pressure_hpa": {"danger_low": 900, "danger_high": 1100},
    },
    "water": {
        "tds": {"warning_high": 1200, "danger_high": 2000},
        "ph": {"warning_low": 6.5, "warning_high": 8.5, "danger_low": 6.0, "danger_high": 9.0},
        "turbidity": {"warning_high": 25, "danger_high": 50},
        "water_temp_c": {"warning_high": 35, "danger_high": 45},
    },
}

# Sudden jump multipliers for rate-of-change detection
JUMP_RULES = {
    "radiation_cpm": {"warn_mult": 5.0, "danger_mult": 10.0, "warn_floor": 120},
    "pm25": {"warn_mult": 4.0, "danger_mult": 8.0, "warn_floor": 50},
    "voc": {"warn_mult": 4.0, "danger_mult": 8.0, "warn_floor": 300},
    "tds": {"warn_mult": 2.0, "danger_mult": 3.5, "warn_floor": 800},
    "turbidity": {"warn_mult": 2.5, "danger_mult": 4.0, "warn_floor": 15},
}

def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))

def _robust_stats(values):
    """Return median and MAD (with fallback to std if needed)."""
    arr = np.array(values, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    if mad == 0:
        std = float(np.std(arr)) or 1e-6
        return med, std, True
    return med, mad, False

def _z_scores(reading, history, features):
    zs = {}
    for feat in features:
        vals = [row[feat] for row in history if row.get(feat) is not None]
        if len(vals) < 3 or reading.get(feat) is None:
            zs[feat] = 0.0
            continue
        med, spread, used_std = _robust_stats(vals)
        scale = spread * (1 if used_std else 1.4826)
        scale = scale if scale != 0 else 1e-6
        zs[feat] = float((reading[feat] - med) / scale)
    return zs

def _iso_probability(model, features, reading):
    vector = [reading.get(f, 0.0) for f in features]
    try:
        score = model.score_samples([vector])[0]
    except Exception:
        return 0.0
    # Lower score = more anomalous; invert and squash
    return float(_sigmoid(-score))

def _stability_factor(history, feat_list, window=50):
    vals = []
    for row in history[-window:]:
        for feat in feat_list:
            v = row.get(feat)
            if v is not None:
                vals.append(v)
    if len(vals) < 5:
        return 1.0
    arr = np.array(vals, dtype=float)
    mean = np.mean(arr)
    spread = np.std(arr)
    if mean == 0:
        return 1.0
    cv = abs(spread / mean)
    if cv < 0.05:
        return 1.0
    if cv > 0.5:
        return 0.3
    return max(0.3, 1.0 - cv)

def _human_summary(node_id, status, reasons):
    if status == "Offline":
        return f"{node_id} is offline; no recent data."
    if not reasons:
        return f"{node_id} is within expected ranges."
    main = reasons[0]
    return f"{node_id}: {main}"

def _check_hard_limits(reading, node_type):
    """Fail-safe guard: immediate danger if absolute thresholds exceeded."""
    reasons = []
    flagged = []
    warn_hit = False
    danger_hit = False
    warn_features = set()
    th = HARD_THRESHOLDS[node_type]
    for feat, limits in th.items():
        val = reading.get(feat)
        if val is None:
            continue
        if "danger_high" in limits and val >= limits["danger_high"]:
            danger_hit = True
            reasons.append(f"Absolute threshold exceeded: {feat} {val} >= {limits['danger_high']}")
            flagged.append({"feature": feat, "value": val, "threshold": limits["danger_high"], "direction": "high"})
        if "danger_low" in limits and val <= limits["danger_low"]:
            danger_hit = True
            reasons.append(f"Absolute threshold exceeded: {feat} {val} <= {limits['danger_low']}")
            flagged.append({"feature": feat, "value": val, "threshold": limits["danger_low"], "direction": "low"})
        if "warning_high" in limits and val >= limits["warning_high"]:
            warn_hit = True
            warn_features.add(feat)
            reasons.append(f"High reading: {feat} {val} >= {limits['warning_high']}")
            flagged.append({"feature": feat, "value": val, "threshold": limits["warning_high"], "direction": "high"})
        if "warning_low" in limits and val <= limits["warning_low"]:
            warn_hit = True
            warn_features.add(feat)
            reasons.append(f"Low reading: {feat} {val} <= {limits['warning_low']}")
            flagged.append({"feature": feat, "value": val, "threshold": limits["warning_low"], "direction": "low"})
    return danger_hit, warn_hit, reasons, flagged, warn_features

def _check_jumps(reading, prev):
    """Detect sudden jumps relative to previous reading for key features."""
    if not prev:
        return [], False, False, []
    reasons = []
    warn_hit = False
    danger_hit = False
    flagged = []
    for feat, rule in JUMP_RULES.items():
        new_val = reading.get(feat)
        old_val = prev.get(feat)
        if new_val is None or old_val is None or old_val == 0:
            continue
        ratio = new_val / old_val if old_val != 0 else float("inf")
        if ratio >= rule["danger_mult"]:
            danger_hit = True
            reasons.append(f"Sudden jump in {feat}: {old_val} -> {new_val} ({ratio:.1f}x)")
            flagged.append({"feature": feat, "prev": old_val, "curr": new_val, "ratio": ratio, "type": "trend"})
        elif ratio >= rule["warn_mult"] and new_val >= rule.get("warn_floor", 0):
            warn_hit = True
            reasons.append(f"Sudden jump in {feat}: {old_val} -> {new_val} ({ratio:.1f}x)")
            flagged.append({"feature": feat, "prev": old_val, "curr": new_val, "ratio": ratio, "type": "trend"})
    return reasons, warn_hit, danger_hit, flagged

def _status_from_prob(p, warn_forced=False, danger_forced=False):
    if danger_forced or p >= 0.7:
        return "Danger"
    if warn_forced or p >= 0.35:
        return "Warning"
    return "Safe"

def predict_node(reading, history, node_id, context=None):
    """Combine physics-based limits + jumps + robust baseline for explainable anomaly scoring."""
    node_type = "ground" if node_id.startswith("ground") else "water"
    features = GROUND_FEATURES if node_type == "ground" else WATER_FEATURES
    history_len = len(history)
    prev = history[-2] if history_len >= 2 else None
    context = context or {}

    # Confidence = history factor * stability factor
    base_history_factor = min(1.0, history_len / 200.0)
    stability_factor = _stability_factor(history, features, window=50)
    confidence = max(0.05, min(1.0, base_history_factor * stability_factor))
    if history_len < 30:
        confidence = min(confidence, 0.4)

    # Layer A: absolute thresholds
    danger_hard, warn_hard, hard_reasons, hard_flagged, warn_features = _check_hard_limits(reading, node_type)

    # Layer B1: rate-of-change checks
    jump_reasons, jump_warn, jump_danger, jump_flagged = _check_jumps(reading, prev)
    jump_boost = 0.0
    if jump_danger:
        jump_boost = max(jump_boost, 0.85)
    if jump_warn:
        jump_boost = max(jump_boost, 0.5)

    # Layer B2: rolling baseline z-scores
    zs = _z_scores(reading, history, features)
    z_max = max(abs(z) for z in zs.values()) if zs else 0.0
    baseline_prob = _sigmoid((z_max - 2.0) * 1.4)
    storm_mode = context.get("storm_mode") if context else False
    if storm_mode and node_type == "water":
        turb_z = abs(zs.get("turbidity", 0))
        if turb_z > 0 and all(k in ["turbidity"] for k in zs.keys()):
            baseline_prob *= 0.8

    # Optional IsolationForest
    iso_prob = 0.0
    iso_used = False
    model_path = GROUND_MODEL_PATH if node_type == "ground" else WATER_MODEL_PATH
    if model_path.exists():
        try:
            model = joblib.load(model_path)
            iso_prob = _iso_probability(model, features, reading)
            iso_used = True
        except Exception:
            iso_prob = 0.0

    abnormal_probability = max(baseline_prob, iso_prob, jump_boost)

    # Multi-sensor bump
    abnormal_count = sum(1 for z in zs.values() if abs(z) >= 2.5) + len(warn_features)
    warn_hit = warn_hard or jump_warn
    danger_hit = jump_danger
    if abnormal_count >= 3:
        abnormal_probability = min(1.0, abnormal_probability + 0.25)
    elif abnormal_count >= 2:
        abnormal_probability = min(1.0, abnormal_probability + 0.15)
    if warn_hard:
        warn_hit = True

    # Enforce probability floors when clear warning/danger cues exist
    if danger_hit:
        abnormal_probability = max(abnormal_probability, 0.8)
    if warn_hit:
        abnormal_probability = max(abnormal_probability, 0.5)
    if danger_hard:
        abnormal_probability = max(abnormal_probability, 0.99)
        danger_hit = True

    # Low-history fail-safe: enforce warning/danger if thresholds hit
    if history_len < 30:
        if warn_hard:
            abnormal_probability = max(abnormal_probability, 0.5)
            warn_hit = True
        if danger_hit:
            abnormal_probability = max(abnormal_probability, 0.8)

    # Status decision
    status = _status_from_prob(abnormal_probability, warn_forced=warn_hit, danger_forced=danger_hit)

    # Confidence floor for clear danger signals
    if status == "Danger":
        confidence = max(confidence, 0.6)

    # Flagged features (top 3 by |z|)
    flagged = jump_flagged.copy()
    for feat, z in sorted(zs.items(), key=lambda item: abs(item[1]), reverse=True):
        flagged.append({
            "feature": feat,
            "z": z,
            "direction": "high" if z >= 0 else "low",
            "value": reading.get(feat),
        })
        if len(flagged) >= 3:
            break

    reasons = []
    # Hard warning reasons already gathered
    reasons.extend(hard_reasons)
    # Jump reasons
    reasons.extend(jump_reasons)
    # Z-score reasons
    for item in flagged:
        if abs(item["z"]) < 2.0:
            continue
        reasons.append(f"{node_id}: {item['feature']} {'high' if item['z']>=0 else 'low'} (z={item['z']:.2f})")

    if abnormal_count >= 2:
        reasons.append(f"Multiple sensors abnormal on {node_id}")
    if not reasons:
        reasons = [f"{node_id}: within expected range"]

    methods = {
        "guardrails": bool(hard_reasons or jump_reasons),
        "rolling_baseline": True,
        "isolation_forest": iso_used,
    }

    return {
        "node_id": node_id,
        "status": status,
        "abnormal_probability": float(abnormal_probability),
        "confidence": float(confidence),
        "reasons": reasons,
        "flagged_features": flagged + hard_flagged,
        "methods": methods,
        "human_summary": _human_summary(node_id, status, reasons),
    }
