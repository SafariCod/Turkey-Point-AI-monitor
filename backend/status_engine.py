from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import time
from typing import Dict, List, Optional, Tuple

from config import AppConfig


NodeId = str


@dataclass
class FeatureVector:
    z_scores: Dict[str, float]
    trend_slopes: Dict[str, float]
    abnormal_count: int


@dataclass
class Interpretation:
    status: str
    confidence: float
    reasons: List[str]
    summary: str


@dataclass
class NodeStatus:
    node_id: NodeId
    status: str
    confidence: float
    reasons: List[str]
    summary: str
    computed_at: str
    latest: Optional[Dict]
    flags: Dict[str, bool]


@dataclass
class StatusCache:
    node_status: Dict[NodeId, NodeStatus]
    overall: Dict
    computed_at_epoch: int


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _mad(values: List[float], med: float) -> float:
    dev = [abs(v - med) for v in values]
    return _median(dev) if dev else 0.0


def _z_scores(reading: Dict, history: List[Dict], features: List[str]) -> Dict[str, float]:
    zs: Dict[str, float] = {}
    for feat in features:
        vals = [row.get(feat) for row in history if row.get(feat) is not None]
        if len(vals) < 5 or reading.get(feat) is None:
            zs[feat] = 0.0
            continue
        vals_f = [float(v) for v in vals]
        med = _median(vals_f)
        mad = _mad(vals_f, med)
        scale = mad * 1.4826 if mad > 0 else (math.sqrt(sum((v - med) ** 2 for v in vals_f) / len(vals_f)) or 1e-6)
        zs[feat] = (float(reading[feat]) - med) / scale
    return zs


def _trend_slopes(history: List[Dict], features: List[str], window: int = 6) -> Dict[str, float]:
    slopes: Dict[str, float] = {}
    if len(history) < 2:
        return {f: 0.0 for f in features}
    h = history[-window:]
    xs = list(range(len(h)))
    x_mean = sum(xs) / len(xs)
    x_var = sum((x - x_mean) ** 2 for x in xs) or 1e-6
    for feat in features:
        ys = [row.get(feat) for row in h if row.get(feat) is not None]
        if len(ys) < 2:
            slopes[feat] = 0.0
            continue
        ys_f = [float(v) for v in ys]
        y_mean = sum(ys_f) / len(ys_f)
        cov = sum((xs[i] - x_mean) * (ys_f[i] - y_mean) for i in range(len(ys_f)))
        slopes[feat] = cov / x_var
    return slopes


def extract_features(reading: Dict, history: List[Dict], features: List[str]) -> FeatureVector:
    zs = _z_scores(reading, history, features)
    slopes = _trend_slopes(history, features)
    abnormal = sum(1 for z in zs.values() if abs(z) >= 2.5)
    return FeatureVector(z_scores=zs, trend_slopes=slopes, abnormal_count=abnormal)


def detect_jumps(history: List[Dict], features: List[str]) -> Tuple[List[str], str | None]:
    if len(history) < 2:
        return [], None
    prev = history[-2]
    curr = history[-1]
    reasons: List[str] = []
    level: str | None = None
    for feat in features:
        a = prev.get(feat)
        b = curr.get(feat)
        if a is None or b is None:
            continue
        try:
            a_f = float(a)
            b_f = float(b)
        except (TypeError, ValueError):
            continue
        if a_f <= 0:
            continue
        ratio = b_f / a_f
        if ratio >= 5.0:
            reasons.append(f"{feat} jump {a_f:.2f} -> {b_f:.2f} ({ratio:.1f}x)")
            if feat == "radiation_cpm":
                level = "danger" if ratio >= 10.0 else "warning"
    return reasons, level


def ai_interpretation(features: FeatureVector, jump_reasons: List[str] | None = None, jump_level: str | None = None) -> Interpretation:
    """AI interpretation placeholder; deterministic fallback for now."""
    benign_low = {"air_temp_c", "pressure_hpa", "humidity"}
    benign_high = {"air_temp_c", "pressure_hpa", "humidity"}
    reasons: List[str] = []
    status = "Safe"
    confidence = 0.4
    summary = "Within expected ranges"

    z_items = list(features.z_scores.items())
    z_max = max((abs(z) for _, z in z_items), default=0.0)
    most_extreme = max(z_items, key=lambda item: abs(item[1]), default=(None, 0.0))
    extreme_feat, extreme_z = most_extreme
    if z_max >= 4.0:
        if (extreme_feat in benign_low and extreme_z < 0) or (extreme_feat in benign_high and extreme_z > 0):
            status = "ABNORMAL"
            confidence = 0.55
            summary = "Unusual pattern versus baseline"
        else:
            status = "Danger"
            confidence = 0.8
            summary = "Major deviation from baseline"
    elif z_max >= 3.0:
        status = "Warning"
        confidence = 0.6
        summary = "Elevated deviation from baseline"
    elif z_max >= 2.5:
        status = "ABNORMAL"
        confidence = 0.55
        summary = "Unusual pattern versus baseline"

    for feat, z in sorted(features.z_scores.items(), key=lambda item: abs(item[1]), reverse=True):
        if abs(z) < 2.5:
            continue
        direction = "high" if z >= 0 else "low"
        reasons.append(f"{feat} {direction} vs baseline (z={z:.2f})")
        if len(reasons) >= 3:
            break

    if jump_reasons:
        reasons = jump_reasons + reasons
        if jump_level == "danger":
            status = "Danger"
            confidence = max(confidence, 0.8)
        elif jump_level == "warning":
            status = "Warning"
            confidence = max(confidence, 0.6)
        else:
            status = "ABNORMAL" if status == "Safe" else status
            confidence = max(confidence, 0.55)
        summary = "Sudden jump detected"

    if not reasons:
        reasons = ["Within expected ranges"]

    return Interpretation(status=status, confidence=confidence, reasons=reasons, summary=summary)


class StatusEngine:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._cache = StatusCache(node_status={}, overall={}, computed_at_epoch=0)

    def _apply_hysteresis(self, node_id: str, new_status: str, now: int, override: bool) -> str:
        if override:
            return new_status
        prev = self._cache.node_status.get(node_id)
        if not prev:
            return new_status
        if new_status == prev.status:
            return new_status
        # Allow faster downgrade to avoid lingering danger after spikes
        if new_status in ("Safe", "ABNORMAL") and prev.status in ("Warning", "Danger"):
            return new_status
        if now - self._cache.computed_at_epoch < self._config.status.hysteresis_sec:
            return prev.status
        return new_status

    def compute_node(self, node_id: str, reading: Dict, history: List[Dict], features: List[str], flags: Dict[str, bool]) -> NodeStatus:
        if flags.get(self._config.behavior.pm25_flag_name):
            features = [f for f in features if f != "pm25"]
        feats = extract_features(reading, history, features)
        jump_reasons, jump_level = detect_jumps(history, features)
        override_hysteresis = bool(jump_reasons) or max((abs(z) for z in feats.z_scores.values()), default=0.0) >= 4.0
        interp = ai_interpretation(feats, jump_reasons, jump_level)
        now = int(time.time())
        status = self._apply_hysteresis(node_id, interp.status, now, override_hysteresis)
        computed_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        return NodeStatus(
            node_id=node_id,
            status=status,
            confidence=interp.confidence,
            reasons=interp.reasons,
            summary=interp.summary,
            computed_at=computed_at,
            latest=reading,
            flags=flags,
        )

    def compute_overall(self, node_results: Dict[NodeId, NodeStatus]) -> Dict:
        now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        if not node_results:
            return {"status": "NO_DATA_YET", "confidence": 0.0, "reasons": [], "summary": "No data", "computed_at": now}
        # aggregate deterministically (not worst-node)
        scores = []
        for node in node_results.values():
            if node.status == "Danger":
                scores.append(1.0)
            elif node.status == "Warning":
                scores.append(0.7)
            elif node.status == "ABNORMAL":
                scores.append(0.5)
            elif node.status == "Offline":
                scores.append(0.6)
            else:
                scores.append(0.2)
        avg = sum(scores) / len(scores)
        features = FeatureVector(
            z_scores={"aggregate_score": avg * 4.0},
            trend_slopes={},
            abnormal_count=sum(1 for s in scores if s >= 0.5),
        )
        interp = ai_interpretation(features)
        return {
            "status": interp.status,
            "confidence": min(1.0, max(interp.confidence, avg)),
            "reasons": interp.reasons + [f"Aggregate risk score {avg:.2f} from {len(scores)} nodes"],
            "summary": interp.summary,
            "computed_at": now,
        }

    def recompute(self, node_histories: Dict[NodeId, List[Dict]], node_features: Dict[NodeId, List[str]], node_flags: Dict[NodeId, Dict[str, bool]]) -> StatusCache:
        now = int(time.time())
        node_results: Dict[NodeId, NodeStatus] = {}
        for node_id, history in node_histories.items():
            if not history:
                continue
            latest = history[-1]
            features = node_features.get(node_id, [])
            flags = node_flags.get(node_id, {})
            node_results[node_id] = self.compute_node(node_id, latest, history, features, flags)
        overall = self.compute_overall(node_results)
        self._cache = StatusCache(node_status=node_results, overall=overall, computed_at_epoch=now)
        return self._cache

    @property
    def cache(self) -> StatusCache:
        return self._cache
