# Render Deployment Checklist (Flask + ESP32 Telemetry)

This repo's Flask entrypoint is `backend/app.py` and the Flask app object is `app`.

## 1) Create the Render service
1. Push this repo to GitHub.
2. In Render, click **New** → **Web Service**.
3. Connect the GitHub repo.
4. Set **Root Directory** to `backend`.
5. Choose **Runtime**: Python.

## 2) Build + start commands (Render)
- **Build Command**:
  ```
  pip install -r requirements.txt
  ```
- **Start Command**:
  ```
  gunicorn app:app --bind 0.0.0.0:$PORT
  ```

Render will inject `PORT`; gunicorn will bind to it.

> Alternative (if you do NOT set Root Directory to `backend`):
> - Build: `pip install -r backend/requirements.txt`
> - Start: `gunicorn backend.app:app --bind 0.0.0.0:$PORT`

## 3) Environment variables
Set in Render → **Environment**:
- `ESP32_API_KEY` = a strong secret string (required)
- `TELEMETRY_HMAC_SECRETS` = JSON map of node_id to secret (required for /api/telemetry)
  - Example: `{"ground_1":"secret1","ground_2":"secret2","ground_3":"secret3"}`
- Optional:
  - `FLASK_DEBUG=0` (recommended for production)
  - `DISABLE_PM25_NODES=ground_2` (comma-separated)
  - `PM25_FALLBACK=1.2`
  - `STATUS_RECOMPUTE_SEC=30`
  - `STATUS_HYSTERESIS_SEC=60`
  - `TELEMETRY_SIG_WINDOW_SEC=300`
  - `TELEMETRY_NONCE_TTL_SEC=600`

## 4) Deploy
1. Click **Create Web Service**.
2. Wait for build/deploy to finish.
3. Copy the service URL (e.g., `https://your-service.onrender.com`).

## 5) Test the telemetry endpoint
Endpoint:
```
POST https://YOUR-SERVICE.onrender.com/api/telemetry
```

### curl test
```bash
curl -X POST "https://YOUR-SERVICE.onrender.com/api/telemetry" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"device_id":"esp32_01","timestamp":1700000000,"data":{"pm25":12.3,"radiation_usvh":0.08}}'
```

Expected response:
```json
{ "ok": true }
```

## 6) ESP32 HTTPClient example
```cpp
HTTPClient http;
http.begin("https://YOUR-SERVICE.onrender.com/api/telemetry");
http.addHeader("Content-Type", "application/json");
http.addHeader("X-API-Key", "YOUR_API_KEY");

String body = "{\"device_id\":\"esp32_01\",\"timestamp\":1700000000,"
              "\"data\":{\"pm25\":12.3,\"radiation_usvh\":0.08}}";
int code = http.POST(body);
String resp = http.getString();
http.end();
```

## 7) Where data is stored
Telemetry JSON lines are appended to:
```
backend/telemetry_log.jsonl
```

Each line is a JSON object with:
- `device_id`
- `timestamp`
- `data`
- `server_received_utc`

## Troubleshooting
- **502 / bad gateway**:
  - Start command likely wrong. Use:
    ```
    gunicorn app:app --bind 0.0.0.0:$PORT
    ```
- **Module not found / import errors**:
  - Build command is wrong or Root Directory not set to `backend`.
  - Ensure `requirements.txt` is in `backend/`.
- **ESP32 gets 401**:
  - Missing or wrong `X-API-Key` header.
  - `ESP32_API_KEY` not set in Render env vars.
- **Service sleeps on free tier**:
  - First request after idle will be slow (cold start). This is expected.
- **Logs**:
  - Render → Service → **Logs** tab.

## Local verification
From `backend/`:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Or gunicorn:
```powershell
python -m pip install -r requirements.txt
gunicorn app:app --bind 0.0.0.0:5000
```
