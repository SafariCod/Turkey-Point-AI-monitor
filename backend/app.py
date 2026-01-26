from flask import Flask, request, jsonify, send_from_directory # type: ignore
from flask_cors import CORS  # type: ignore
from pathlib import Path
from datetime import datetime
import json
import os
from db import init_db, insert_reading, get_recent, get_history, prune_old, insert_event, get_events, get_latest
from ai.predict import predict_node

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
CORS(app, resources={r"/api/*": {"origins": [r"http://127\\.0\\.0\\.1:\\d+", r"http://localhost:\\d+","http://127.0.0.1","http://localhost"]}})
init_db()

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
    result = predict_node(latest, history, node_id)
    result["latest"] = latest
    result["inserted_id"] = inserted_id
    return jsonify(result)

@app.post("/api/telemetry")
def telemetry():
    expected_key = os.getenv("ESP32_API_KEY")
    if not expected_key:
        return jsonify({"error": "ESP32_API_KEY not set on server"}), 500
    if request.headers.get("X-API-Key") != expected_key:
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body required"}), 400

    device_id = payload.get("device_id")
    timestamp = payload.get("timestamp")
    data = payload.get("data")

    if not isinstance(device_id, str) or not device_id.strip():
        return jsonify({"error": "device_id must be a non-empty string"}), 400
    if not isinstance(timestamp, (int, float)):
        return jsonify({"error": "timestamp must be numeric"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "data must be an object"}), 400

    record = {
        "device_id": device_id.strip(),
        "timestamp": timestamp,
        "data": data,
        "server_received_utc": datetime.utcnow().isoformat() + "Z",
    }
    with open(TELEMETRY_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    return jsonify({"ok": True})

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
    storm_mode = False
    storm_reason = None
    correlation_reason = None
    any_data = False

    # Preload humidity info for context
    ground_humid_high = False
    for node_id in NODE_IDS:
        if node_id.startswith("ground"):
            hist = get_history(node_id, n=2)
            if hist and hist[-1].get("humidity") is not None and hist[-1]["humidity"] >= 90:
                ground_humid_high = True
    water_history_for_context = get_history("water_1", n=3)

    for node_id in NODE_IDS:
        history = get_history(node_id, n=200)
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
        # Offline detection
        ts = latest.get("ts")
        offline = False
        if ts:
            try:
                from datetime import datetime, timezone
                ts_clean = ts.replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_clean)
                if (datetime.utcnow() - dt.replace(tzinfo=None)).total_seconds() > OFFLINE_SECONDS:
                    offline = True
            except Exception:
                pass

        node_result = predict_node(latest, history, node_id, context={"humidity_high": ground_humid_high, "storm_mode": storm_mode, "storm_reason": storm_reason})
        node_result["latest"] = latest
        if offline:
            node_result["status"] = "Offline"
            node_result["reasons"].append(f"No data received from {node_id} in last {OFFLINE_SECONDS//60} minutes")
            node_result["abnormal_probability"] = max(node_result["abnormal_probability"], 0.7)
            node_result["confidence"] = max(node_result["confidence"], 0.5)
            node_result["human_summary"] = f"{node_id} is offline; no recent data."
            insert_event("Warning", node_id, "offline", node_result["reasons"][-1], node_result["abnormal_probability"])
        else:
            node_result["human_summary"] = node_result.get("human_summary") or "Status assessed"

        nodes[node_id] = node_result
        nodes[node_id]["latest"] = latest
        if latest.get("ts"):
            latest_ts = max(latest_ts or latest["ts"], latest["ts"])
        if node_result["status"] in ("Warning", "Danger"):
            insert_event(node_result["status"], node_id, "anomaly", "; ".join(node_result["reasons"][:2]), node_result["abnormal_probability"])

    if not any_data:
        return jsonify({
            "ok": True,
            "status": "NO_DATA_YET",
            "latest": None,
            "nodes": nodes,
            "last_updated_ts": None,
        })

    # Cross-node correlation: regional radiation
    from datetime import datetime, timedelta
    correlation_hit = False
    now = datetime.utcnow()
    recent_window = now - timedelta(minutes=5)
    high_nodes = []
    for nid, data in nodes.items():
        data = data or {}
        if not nid.startswith("ground"):
            continue
        latest = data.get("latest") or {}
        ts = latest.get("ts")
        if ts:
            try:
                ts_clean = ts.replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_clean)
                if dt < recent_window:
                    continue
            except Exception:
                continue
        if data.get("abnormal_probability", 0) >= 0.7:
            high_nodes.append(nid)
    if len(high_nodes) >= 2:
        correlation_hit = True
        correlation_reason = f"Regional radiation anomaly across {', '.join(high_nodes)}"
    elif len(high_nodes) == 1:
        correlation_reason = f"Localized anomaly near {high_nodes[0]}"

    # Overall = worst abnormal probability
    overall = max(nodes.values(), key=lambda n: n["abnormal_probability"], default=None)
    overall_status = overall["status"] if overall else "Safe"
    overall_prob = overall["abnormal_probability"] if overall else 0.0
    overall_reasons = []
    if overall:
        overall_reasons.extend(overall.get("reasons", []))
    if correlation_reason:
        overall_reasons.append(correlation_reason)
        overall_prob = min(1.0, overall_prob + 0.1)
        insert_event("Warning", "all", "correlation", correlation_reason, overall_prob)
    if storm_mode and storm_reason:
        insert_event("Info", "water_1", "context", f"Storm mode active: {storm_reason}", overall_prob)
    if storm_mode and storm_reason:
        overall_reasons.append(f"Context: {storm_reason}")
    if any(n["status"] == "Offline" for n in nodes.values()):
        if overall_status == "Safe":
            overall_status = "Warning"
        overall_reasons.append("One or more nodes offline")

    overall_human = overall.get("human_summary") if overall else ""
    return jsonify({
        "overall_status": overall_status,
        "overall_abnormal_probability": overall_prob,
        "overall_reasons": overall_reasons,
        "overall_human_summary": overall_human,
        "nodes": nodes,
        "context": {"storm_mode": storm_mode, "reason": storm_reason} if storm_mode else {"storm_mode": False},
        "last_updated_ts": latest_ts,
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
