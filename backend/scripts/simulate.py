import json
import random
import time
from datetime import datetime
from urllib import request

API_URL = "http://127.0.0.1:5000/api/ingest"

GROUND_NODES = ["ground_1", "ground_2", "ground_3"]
WATER_NODE = "water_1"
OFFLINE_SECONDS = 120

offline_state = {"ground_3": False, "resume_at": None}
storm_phase = 0

def send(payload):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(API_URL, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, body

def ground_reading(node_id):
    reading = {
        "node_id": node_id,
        "radiation_cpm": round(random.gauss(28, 4), 2),
        "pm25": round(random.gauss(12, 3), 2),
        "air_temp_c": round(random.gauss(27, 2), 2),
        "humidity": round(random.gauss(65, 5), 2),
        "pressure_hpa": round(random.gauss(1010, 3), 2),
        "voc": round(random.gauss(180, 40), 2),
        "ts": datetime.utcnow().isoformat(),
    }

    if random.random() < 0.05:
        choice = random.choice(["radiation", "pm25", "voc"])
        if choice == "radiation":
            reading["radiation_cpm"] = round(random.uniform(400, 1200), 2)
        elif choice == "pm25":
            reading["pm25"] = round(random.uniform(120, 220), 2)
        elif choice == "voc":
            reading["voc"] = round(random.uniform(500, 900), 2)
    return reading

def regional_radiation_spike():
    print("Injecting regional radiation anomaly across ground_1 and ground_2")
    return [
        {**ground_reading("ground_1"), "radiation_cpm": round(random.uniform(600, 1200), 2)},
        {**ground_reading("ground_2"), "radiation_cpm": round(random.uniform(600, 1200), 2)},
    ]

def water_reading():
    global storm_phase
    reading = {
        "node_id": WATER_NODE,
        "tds": round(random.gauss(450, 40), 1),
        "ph": round(random.gauss(7.4, 0.2), 2),
        "turbidity": round(random.gauss(6, 1.5), 2),
        "water_temp_c": round(random.gauss(26, 1.5), 2),
        "ts": datetime.utcnow().isoformat(),
    }

    # Storm simulation phases (gradual then spike)
    if storm_phase > 0:
        reading["turbidity"] = round(reading["turbidity"] + storm_phase * 4, 2)
        if storm_phase >= 3:
            reading["tds"] = round(random.uniform(900, 1500), 1)
        storm_phase = storm_phase - 1

    if random.random() < 0.05:
        choice = random.choice(["tds", "turbidity"])
        if choice == "tds":
            reading["tds"] = round(random.uniform(900, 1600), 1)
        else:
            reading["turbidity"] = round(random.uniform(20, 60), 2)
    return reading

def maybe_storm():
    global storm_phase
    if random.random() < 0.02 and storm_phase == 0:
        storm_phase = 4
        print("Simulating water storm event (turbidity rising)")

def maybe_offline(now):
    if offline_state["ground_3"]:
        if offline_state["resume_at"] and now >= offline_state["resume_at"]:
            offline_state["ground_3"] = False
            offline_state["resume_at"] = None
            print("Resuming ground_3 after offline simulation")
        else:
            return True
    else:
        if random.random() < 0.01:
            offline_state["ground_3"] = True
            offline_state["resume_at"] = now + OFFLINE_SECONDS + 20
            print("Pausing ground_3 to simulate offline")
    return offline_state["ground_3"]

def main():
    print(f"Sending readings to {API_URL} every 3 seconds (Ctrl+C to stop)")
    tick = 0
    while True:
        tick += 1
        now = time.time()
        maybe_storm()
        payloads = [water_reading()]
        # Regional spike occasionally
        if random.random() < 0.02:
            payloads.extend(regional_radiation_spike())
        else:
            payloads.extend(ground_reading(n) for n in ["ground_1", "ground_2"])

        offline = maybe_offline(now)
        if not offline:
            payloads.append(ground_reading("ground_3"))

        for p in payloads:
            try:
                status, body = send(p)
                print(f"[{p['ts']}] {p['node_id']} -> {status} {body}")
            except Exception as e:
                print(f"Error sending {p['node_id']}: {e}")
        time.sleep(3)

if __name__ == "__main__":
    main()
