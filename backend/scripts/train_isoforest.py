import sqlite3
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "readings.db"
AI_DIR = BASE_DIR / "ai"

GROUND_FEATURES = ["radiation_cpm", "pm25", "air_temp_c", "humidity", "pressure_hpa", "voc"]
WATER_FEATURES = ["tds", "ph", "turbidity", "water_temp_c"]

MIN_ROWS = 200
CONTAMINATION = 0.05
N_ESTIMATORS = 200
RANDOM_STATE = 42


def fetch_rows(node_ids, features, limit=5000):
    placeholders = ",".join(["?"] * len(node_ids))
    query = f"""
    SELECT {', '.join(features)}
    FROM readings
    WHERE node_id IN ({placeholders})
    ORDER BY id DESC
    LIMIT ?
    """
    params = [*node_ids, limit]
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(query, params)
        rows = cur.fetchall()
    # Filter out rows with any NULLs
    filtered = [row for row in rows if all(val is not None for val in row)]
    return np.array(filtered, dtype=float)


def train_and_save(data, features, path):
    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=CONTAMINATION,
        random_state=RANDOM_STATE,
    )
    model.fit(data)
    AI_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    print(f"Saved model: {path} ({data.shape[0]} rows, {data.shape[1]} features)")


def main():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run the simulator first.")
        return

    ground_data = fetch_rows(["ground_1", "ground_2", "ground_3"], GROUND_FEATURES)
    if len(ground_data) < MIN_ROWS:
        print(f"Ground: not enough rows ({len(ground_data)}). Need at least {MIN_ROWS}.")
    else:
        train_and_save(ground_data, GROUND_FEATURES, AI_DIR / "isoforest_ground.pkl")

    water_data = fetch_rows(["water_1"], WATER_FEATURES)
    if len(water_data) < MIN_ROWS:
        print(f"Water: not enough rows ({len(water_data)}). Need at least {MIN_ROWS}.")
    else:
        train_and_save(water_data, WATER_FEATURES, AI_DIR / "isoforest_water.pkl")


if __name__ == "__main__":
    main()
