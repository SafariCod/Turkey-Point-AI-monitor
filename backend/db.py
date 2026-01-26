import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "readings.db"
MAX_ROWS = 5000

# Wide table covering all sensors across all nodes
COL_TYPES = {
    "ts": "TEXT",
    "node_id": "TEXT",
    "radiation_cpm": "REAL",
    "pm25": "REAL",
    "air_temp_c": "REAL",
    "humidity": "REAL",
    "pressure_hpa": "REAL",
    "voc": "REAL",
    "tds": "REAL",
    "ph": "REAL",
    "turbidity": "REAL",
    "water_temp_c": "REAL",
}
COLS = list(COL_TYPES.keys())

EVENT_COLS = ["id", "ts", "level", "node_id", "event_type", "message", "abnormal_probability"]

def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            node_id TEXT,
            radiation_cpm REAL,
            pm25 REAL,
            air_temp_c REAL,
            humidity REAL,
            pressure_hpa REAL,
            voc REAL,
            tds REAL,
            ph REAL,
            turbidity REAL,
            water_temp_c REAL
        )
        """)
        _ensure_columns(con)
        con.execute("CREATE INDEX IF NOT EXISTS idx_readings_id ON readings(id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_readings_node_id ON readings(node_id, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_readings_node_ts ON readings(node_id, ts)")
        con.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            level TEXT,
            node_id TEXT,
            event_type TEXT,
            message TEXT,
            abnormal_probability REAL
        )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")

def _ensure_columns(con):
    """Add missing columns if the DB already existed with an older schema."""
    cur = con.execute("PRAGMA table_info(readings)")
    existing = {row[1] for row in cur.fetchall()}
    for col in COLS:
        if col not in existing:
            con.execute(f"ALTER TABLE readings ADD COLUMN {col} {COL_TYPES[col]}")

def insert_reading(r):
    ts = r.get("ts") or datetime.utcnow().isoformat() + "Z"
    values = []
    for col in COLS:
        if col == "ts":
            values.append(ts)
        else:
            values.append(r.get(col))
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(f"""
        INSERT INTO readings ({', '.join(COLS)})
        VALUES ({', '.join(['?'] * len(COLS))})
        """, tuple(values))
        return cur.lastrowid

def get_recent(n=200, node_id=None, include_baseline=False, features=None):
    query = f"""
    SELECT {', '.join(['id'] + COLS)}
    FROM readings
    """
    params = []
    if node_id:
        query += " WHERE node_id = ?"
        params.append(node_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(n)

    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(query, tuple(params))
    rows = cur.fetchall()

    rows = list(reversed(rows))
    keys = ["id"] + COLS
    data = [dict(zip(keys, row)) for row in rows]
    baseline = None
    if include_baseline and data:
        feats = features or []
        baseline = {}
        for feat in feats:
            vals = [r.get(feat) for r in data if r.get(feat) is not None]
            if vals:
                baseline[feat] = float(sum(vals) / len(vals))
    return data if not include_baseline else (data, baseline)

def get_history(node_id, n=200):
    return get_recent(n=n, node_id=node_id)

def get_latest(node_id):
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(f"""
        SELECT id, {', '.join(COLS)}
        FROM readings
        WHERE node_id = ?
        ORDER BY id DESC
        LIMIT 1
        """, (node_id,))
        row = cur.fetchone()
    if not row:
        return None
    keys = ["id"] + COLS
    return dict(zip(keys, row))

def insert_event(level, node_id, event_type, message, abnormal_probability):
    ts = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
        INSERT INTO events (ts, level, node_id, event_type, message, abnormal_probability)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (ts, level, node_id, event_type, message, abnormal_probability))

def get_events(n=50):
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("""
        SELECT id, ts, level, node_id, event_type, message, abnormal_probability
        FROM events ORDER BY id DESC LIMIT ?
        """, (n,))
        rows = cur.fetchall()
    return [dict(zip(EVENT_COLS, row)) for row in rows]

def prune_old():
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT COUNT(*) FROM readings")
        count = cur.fetchone()[0]
        if count > MAX_ROWS:
            to_delete = count - MAX_ROWS
            con.execute("""
            DELETE FROM readings WHERE id IN (
                SELECT id FROM readings ORDER BY id ASC LIMIT ?
            )
            """, (to_delete,))
