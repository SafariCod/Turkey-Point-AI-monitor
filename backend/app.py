from flask import Flask, request, jsonify, send_from_directory # type: ignore
from flask_cors import CORS  # type: ignore
from pathlib import Path
from datetime import datetime
import json
import os
import time
from db import init_db, insert_reading, get_recent, get_history, prune_old, insert_event, get_events, get_latest
from config import load_config
from security import NonceCache, verify_signature
from status_engine import StatusEngine
from ingest_utils import normalize_reading

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
CORS(app, resources={r"/api/*": {"origins": [r"http://127\\.0\\.0\\.1:\\d+", r"http://localhost:\\d+","http://127.0.0.1","http://localhost"]}})
init_db()
APP_CONFIG = load_config()
NONCE_CACHE = NonceCache(APP_CONFIG.security.nonce_ttl_sec)
STATUS_ENGINE = StatusEngine(APP_CONFIG)

GROUND_FIELDS = ["radiation_cpm", "pm25", "air_temp_c", "humidity", "pressure_hpa", "voc"]
WATER_FIELDS = ["tds", "ph", "turbidity", "water_temp_c"]
ALL_FIELDS = set(GROUND_FIELDS + WATER_FIELDS)
NODE_IDS = ["ground_1", "ground_2", "ground_3", "water_1"]
OFFLINE_SECONDS = 120
last_ingest_snapshot = {
    "received": None,
    "stored": None,
    "inserted_id": None,
    "server_received_utc": None,
}
TELEMETRY_LOG_PATH = BASE_DIR / "telemetry_log.jsonl"

@app.get("/api/health")
def health():
    return jsonify({"ok": True})

@app.get("/api/time")
def time_endpoint():
    return jsonify({"epoch": int(time.time())})

@app.post("/api/ingest")
def ingest():
    # TODO: add auth/rate limiting for ingest when moving beyond demo
    raw_body = request.get_data(as_text=True)
    server_received_utc = datetime.utcnow().isoformat() + "Z"
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400

    node_id = data.get("node_id")
    if node_id not in NODE_IDS:
        return jsonify({"error": "node_id must be one of ground_1, ground_2, ground_3, water_1"}), 400

    required = GROUND_FIELDS if node_id.startswith("ground") else WATER_FIELDS
    missing = [k for k in required if data.get(k) is None]
    if missing:
        return jsonify({"error": f"Missing fields for {node_id}: {', '.join(missing)}"}), 400

    ts_in = data.get("ts") or datetime.utcnow().isoformat() + "Z"
    reading = {"ts": ts_in, "node_id": node_id}
    try:
        for field in ALL_FIELDS:
            if data.get(field) is None:
                reading[field] = None
            else:
                reading[field] = float(data[field])
    except (TypeError, ValueError):
        return jsonify({"error": "Numeric fields must be numbers"}), 400
    reading, flags = normalize_reading(node_id, reading, ts_in, list(ALL_FIELDS), APP_CONFIG)

    defaulted = [f for f in ALL_FIELDS if data.get(f) is None]
    if defaulted:
        app.logger.info(f"INGEST DEFAULTED fields to None: {', '.join(defaulted)}")

    inserted_id = insert_reading(reading)
    prune_old()
    normalized = {"id": inserted_id, **reading}
    app.logger.info(f"INGEST RECEIVED: {raw_body}")
    app.logger.info(f"INGEST STORED: {normalized}")
    last_ingest_snapshot.update({
        "received": raw_body,
        "stored": normalized,
        "inserted_id": inserted_id,
        "server_received_utc": server_received_utc,
    })

    history = get_history(node_id, n=200)
    latest = history[-1] if history else reading
    node_flags = {node_id: flags}
    node_features = {node_id: (GROUND_FIELDS if node_id.startswith("ground") else WATER_FIELDS)}
    STATUS_ENGINE.recompute({node_id: history}, node_features, node_flags)
    result = STATUS_ENGINE.cache.node_status.get(node_id)
    payload = {
        "node_id": node_id,
        "status": result.status if result else "Safe",
        "confidence": result.confidence if result else 0.0,
        "reasons": result.reasons if result else [],
        "human_summary": result.summary if result else "",
        "latest": latest,
        "flags": flags,
        "computed_at": result.computed_at if result else datetime.utcnow().isoformat() + "Z",
        "inserted_id": inserted_id,
    }
    return jsonify(payload)

@app.post("/api/telemetry")
def telemetry():
    expected_key = os.getenv("ESP32_API_KEY")
    if not expected_key:
        return jsonify({"error": "ESP32_API_KEY not set on server"}), 500
    if request.headers.get("X-API-Key") != expected_key:
        return jsonify({"error": "Unauthorized"}), 401

    body_bytes = request.get_data()
    sig_result = verify_signature(dict(request.headers), body_bytes, APP_CONFIG.security, NONCE_CACHE)
    if not sig_result.ok:
        return jsonify({"error": sig_result.error}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body required"}), 400

    device_id = payload.get("device_id")
    node_id = payload.get("node_id")
    data = payload.get("data")
    ts_in = payload.get("timestamp") or payload.get("ts")

    if isinstance(device_id, str) and device_id.strip():
        node_id = device_id.strip()
    elif isinstance(node_id, str) and node_id.strip():
        node_id = node_id.strip()
    else:
        return jsonify({"error": "device_id or node_id required"}), 400

    if sig_result.timestamp:
        server_ts = int(sig_result.timestamp)
    elif isinstance(ts_in, (int, float)) and ts_in > 0:
        server_ts = int(ts_in)
    else:
        server_ts = int(time.time())
    ts_iso = datetime.utcfromtimestamp(server_ts).replace(microsecond=0).isoformat() + "Z"

    if not isinstance(data, dict):
        data = payload

    reading, flags = normalize_reading(node_id, data, ts_iso, GROUND_FIELDS, APP_CONFIG)
    insert_reading(reading)
    prune_old()

    record = {
        "device_id": node_id,
        "timestamp": server_ts,
        "data": data,
        "server_received_utc": datetime.utcnow().isoformat() + "Z",
    }
    with open(TELEMETRY_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    return jsonify({"ok": True, "node_id": node_id, "ts": ts_iso, "flags": flags})

@app.get("/api/recent")
def recent():
    try:
        n = int(request.args.get("n", 200))
    except ValueError:
        return jsonify({"error": "Query param n must be an integer"}), 400
    n = max(1, min(n, 1000))
    node_id = request.args.get("node_id")
    include_baseline = request.args.get("include_baseline") in ("1", "true", "yes")
    if include_baseline:
        rows, baseline = get_recent(n, node_id=node_id, include_baseline=True, features=[
            "radiation_cpm","pm25","air_temp_c","humidity","pressure_hpa","voc","tds","ph","turbidity","water_temp_c"
        ])
        return jsonify({"rows": rows, "baseline": baseline or {}})
    return jsonify({"rows": get_recent(n, node_id=node_id)})

@app.get("/api/status")
def status():
    nodes = {}
    latest_ts = None
    any_data = False
    now = int(time.time())

    node_histories = {}
    node_flags = {}
    node_features = {}
    for node_id in NODE_IDS:
        history = get_history(node_id, n=200)
        node_histories[node_id] = history
        if not history:
            nodes[node_id] = {
                "node_id": node_id,
                "status": "Offline",
                "abnormal_probability": 0.0,
                "confidence": 0.0,
                "reasons": [f"No data received from {node_id} yet"],
                "flagged_features": [],
                "latest": None,
                "human_summary": "No data yet",
            }
            continue

        latest = history[-1]
        any_data = True
        node_features[node_id] = GROUND_FIELDS if node_id.startswith("ground") else WATER_FIELDS
        if node_id in APP_CONFIG.behavior.disable_pm25_nodes:
            node_features[node_id] = [f for f in node_features[node_id] if f != "pm25"]
            node_flags[node_id] = {APP_CONFIG.behavior.pm25_flag_name: True}
        else:
            node_flags[node_id] = {}

        if latest.get("ts"):
            latest_ts = max(latest_ts or latest["ts"], latest["ts"])
        nodes[node_id] = nodes.get(node_id, {"node_id": node_id})
        nodes[node_id]["latest"] = latest

    if not any_data:
        return jsonify({
            "ok": True,
            "status": "NO_DATA_YET",
            "latest": None,
            "nodes": nodes,
            "last_updated_ts": None,
        })

    if now - STATUS_ENGINE.cache.computed_at_epoch >= APP_CONFIG.status.recompute_interval_sec:
        STATUS_ENGINE.recompute(node_histories, node_features, node_flags)

    status_cache = STATUS_ENGINE.cache
    for node_id, history in node_histories.items():
        if node_id not in status_cache.node_status and history:
            node_result = STATUS_ENGINE.compute_node(
                node_id,
                history[-1],
                history,
                node_features.get(node_id, []),
                node_flags.get(node_id, {}),
            )
            status_cache.node_status[node_id] = node_result
    for node_id, node_result in status_cache.node_status.items():
        nodes[node_id] = {
            "node_id": node_id,
            "status": node_result.status,
            "abnormal_probability": node_result.confidence,
            "confidence": node_result.confidence,
            "reasons": node_result.reasons,
            "flagged_features": [],
            "latest": node_result.latest,
            "human_summary": node_result.summary,
            "computed_at": node_result.computed_at,
            "flags": node_result.flags,
        }

    # Offline detection (no recent data)
    for node_id, data in nodes.items():
        latest = (data or {}).get("latest") or {}
        ts = latest.get("ts")
        if not ts:
            continue
        try:
            ts_clean = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_clean)
            if (datetime.utcnow() - dt.replace(tzinfo=None)).total_seconds() > OFFLINE_SECONDS:
                data["status"] = "Offline"
                data["reasons"] = (data.get("reasons") or []) + [f"No data received from {node_id} in last {OFFLINE_SECONDS//60} minutes"]
        except Exception:
            continue

    return jsonify({
        "overall_status": status_cache.overall.get("status", "Safe"),
        "overall_abnormal_probability": status_cache.overall.get("confidence", 0.0),
        "overall_reasons": status_cache.overall.get("reasons", []),
        "overall_human_summary": status_cache.overall.get("summary", ""),
        "nodes": nodes,
        "context": {"storm_mode": False},
        "last_updated_ts": latest_ts,
        "computed_at": status_cache.overall.get("computed_at"),
    })

@app.get("/api/events")
def events():
    try:
        n = int(request.args.get("n", 50))
    except ValueError:
        return jsonify({"error": "Query param n must be an integer"}), 400
    n = max(1, min(n, 200))
    return jsonify({"events": get_events(n)})

@app.get("/api/latest_all")
def latest_all():
    """Return the latest reading per node (all four nodes)."""
    results = []
    for node_id in NODE_IDS:
        latest = get_latest(node_id)
        results.append({"node_id": node_id, "latest": latest})
    return jsonify({"rows": results})

@app.get("/api/latest/<node_id>")
def latest_one(node_id):
    if node_id not in NODE_IDS:
        return jsonify({"error": f"Unknown node_id {node_id}"}), 400
    latest = get_latest(node_id)
    return jsonify({"latest": latest})

@app.get("/api/debug/last_ingest")
def debug_last_ingest():
    return jsonify(last_ingest_snapshot)

@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")

@app.get("/<path:path>")
def static_proxy(path):
    target = FRONTEND_DIR / path
    if target.exists():
        return send_from_directory(FRONTEND_DIR, path)
    return jsonify({"error": "Not found"}), 404

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
