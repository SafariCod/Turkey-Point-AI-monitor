#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <time.h>
#include <ArduinoJson.h>
#include <Adafruit_BME680.h>
#include <Wire.h>

#include "config.h"

// SDS011 constants
static const uint8_t SDS_FRAME_LEN = 10;
static const uint8_t SDS_HEADER1 = 0xAA;
static const uint8_t SDS_HEADER2 = 0xC0;
static const uint8_t SDS_TAIL = 0xAB;

HardwareSerial sdsSerial(2); // UART2
Adafruit_BME680 bme;

float lastPm25 = 12.0f;
float lastPm10 = 0.0f;
float lastTempC = 24.0f;
float lastHum = 55.0f;
float lastPress = 1010.0f;
float lastGas = 100000.0f;
float lastRadiationUsvh = 0.0f;
unsigned long bmeRetryAt = 0;
unsigned long bmeWarmupUntil = 0;
bool bmeReady = false;
uint8_t bmeAddrInUse = BME680_I2C_ADDR;
unsigned long bootMs = 0;
unsigned long sdsWarmupUntil = 0;
unsigned long sdsDebugWindowEnd = 0;
unsigned long sdsNoFrameHintAt = 0;
unsigned long sdsLastGoodFrameMs = 0;
bool sdsHintShown = false;
volatile uint32_t geigerPulses = 0;
unsigned long geigerWindowStart = 0;
portMUX_TYPE geigerMux = portMUX_INITIALIZER_UNLOCKED;

void rawSdsDebugWindow();
bool bootstrapTimeIfNeeded();
bool trySyncTimeNtp();

void IRAM_ATTR onGeigerPulse() {
  portENTER_CRITICAL_ISR(&geigerMux);
  geigerPulses++;
  portEXIT_CRITICAL_ISR(&geigerMux);
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("Connecting to WiFi SSID=%s\n", WIFI_SSID);
  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 40) {
    delay(250);
    Serial.print(".");
    retries++;
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("WiFi connected. IP: %s\n", WiFi.localIP().toString().c_str());
    IPAddress dns1(1, 1, 1, 1);
    IPAddress dns2(8, 8, 8, 8);
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE, dns1, dns2);
    Serial.println("DNS set to 1.1.1.1 and 8.8.8.8");
    bootstrapTimeIfNeeded();
    trySyncTimeNtp();
  } else {
    Serial.println("WiFi connection failed; retrying in 3 seconds");
    delay(3000);
    connectWiFi();
  }
}

void setupTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov", "time.google.com");
}

int monthFromString(const char *mon) {
  static const char *months[] = {"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"};
  for (int i = 0; i < 12; i++) {
    if (strncmp(mon, months[i], 3) == 0) return i;
  }
  return -1;
}

bool bootstrapTimeIfNeeded() {
  time_t now = time(nullptr);
  if (now >= 1700000000) return true;

  char mon[4] = {0};
  int day = 0;
  int year = 0;
  int hour = 0, minute = 0, second = 0;
  if (sscanf(__DATE__, "%3s %d %d", mon, &day, &year) != 3) return false;
  if (sscanf(__TIME__, "%d:%d:%d", &hour, &minute, &second) != 3) return false;
  int month = monthFromString(mon);
  if (month < 0) return false;

  struct tm tm_time = {};
  tm_time.tm_year = year - 1900;
  tm_time.tm_mon = month;
  tm_time.tm_mday = day;
  tm_time.tm_hour = hour;
  tm_time.tm_min = minute;
  tm_time.tm_sec = second;
  time_t compileTime = mktime(&tm_time);
  if (compileTime < 1700000000) return false;

  struct timeval tv;
  tv.tv_sec = compileTime;
  tv.tv_usec = 0;
  settimeofday(&tv, nullptr);
  Serial.printf("Bootstrap time set from compile time, epoch=%ld\n", static_cast<long>(compileTime));
  return true;
}

bool trySyncTimeNtp() {
  setupTime();
  struct tm timeinfo;
  unsigned long start = millis();
  while (millis() - start < 10000) {
    if (getLocalTime(&timeinfo, 1000)) {
      time_t now = time(nullptr);
      Serial.printf("NTP check epoch=%ld\n", static_cast<long>(now));
      if (now >= 1700000000) {
        Serial.printf("Time synced via NTP, epoch=%ld\n", static_cast<long>(now));
        return true;
      }
    }
  }
  Serial.println("NTP sync failed; time not set yet");
  return false;
}

bool ensureTimeSynced() {
  time_t now = time(nullptr);
  if (now >= 1700000000) return true;
  if (bootstrapTimeIfNeeded()) return true;
  return trySyncTimeNtp();
}

bool checkInternetReachable(IPAddress &resolvedIp) {
  Serial.println("Resolving host: turkey-point-ai-monitor.onrender.com");
  if (WiFi.hostByName("turkey-point-ai-monitor.onrender.com", resolvedIp)) {
    Serial.printf("DNS resolved to %s\n", resolvedIp.toString().c_str());
  } else {
    Serial.println("DNS resolution failed");
    return false;
  }

  WiFiClient tcp;
  Serial.println("Checking TCP 443 reachability...");
  if (!tcp.connect(resolvedIp, 443)) {
    Serial.println("TCP 443 connection failed");
    tcp.stop();
    return false;
  }
  tcp.stop();
  Serial.println("TCP 443 reachable");
  return true;
}

bool beginHttps(HTTPClient &http, WiFiClientSecure &client) {
  const String url = String(SERVER_URL);
  int schemePos = url.indexOf("://");
  int hostStart = schemePos >= 0 ? schemePos + 3 : 0;
  int pathStart = url.indexOf('/', hostStart);
  String host = pathStart >= 0 ? url.substring(hostStart, pathStart) : url.substring(hostStart);
  String path = pathStart >= 0 ? url.substring(pathStart) : "/";
  if (host.length() == 0) {
    Serial.println("HTTPS begin failed: host empty");
    return false;
  }
  client.setHandshakeTimeout(15000);
  Serial.printf("HTTPS host=%s path=%s\n", host.c_str(), path.c_str());
  return http.begin(client, host.c_str(), 443, path.c_str(), true);
}

void i2cScan() {
  Serial.println("I2C scan...");
  byte count = 0;
  for (byte addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    byte err = Wire.endTransmission();
    if (err == 0) {
      Serial.printf(" - Found device at 0x%02X\n", addr);
      count++;
    }
  }
  if (count == 0) Serial.println(" - No I2C devices found");
}

bool initBME() {
  bool found = false;
  uint8_t addrs[2] = {BME680_I2C_ADDR, BME680_I2C_ADDR_ALT};
  for (uint8_t addr : addrs) {
    if (addr == 0) continue;
    Serial.printf("Trying BME680 at 0x%02X...\n", addr);
    if (bme.begin(addr)) {
      bmeAddrInUse = addr;
      found = true;
      Serial.printf("BME680 detected at 0x%02X\n", addr);
      break;
    }
  }
  if (!found) {
    Serial.println("BME680 not detected on I2C.");
    return false;
  }
  bme.setTemperatureOversampling(BME680_OS_8X);
  bme.setHumidityOversampling(BME680_OS_2X);
  bme.setPressureOversampling(BME680_OS_4X);
  bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
  bme.setGasHeater(320, 150);
  return true;
}

bool readBME(float &tempC, float &hum, float &press, float &gas) {
  if (!bme.performReading()) {
    Serial.println("BME680 read failed");
    return false;
  }
  tempC = bme.temperature;
  hum = bme.humidity;
  press = bme.pressure / 100.0f; // Pa -> hPa
  gas = bme.gas_resistance;      // Ohms
  return true;
}

// Simple monotonic mapping from gas resistance to pseudo-VOC (placeholder)
float gasToVoc(float gasOhms) {
  // Typical clean air ~100k-500k Ohms; map to ~100-300 ppb-like scale.
  if (gasOhms <= 0) return 150.0f;
  float logVal = log10(gasOhms);
  float voc = 50.0f + (logVal * 80.0f); // tweak to ~100-300 range
  if (voc < 50.0f) voc = 50.0f;
  if (voc > 800.0f) voc = 800.0f; // cap
  return voc;
}

bool readSDS(float &pm25, float &pm10) {
  // Attempt to resync and parse SDS011 10-byte frames without blocking long
  const unsigned long start = millis();
  while (millis() - start < SDS_READ_WINDOW_MS) {
    if (!sdsSerial.available()) {
      delay(2);
      continue;
    }

    int first = sdsSerial.read();
    if (first < 0 || static_cast<uint8_t>(first) != SDS_HEADER1) continue;

    uint8_t buf[SDS_FRAME_LEN];
    buf[0] = SDS_HEADER1;
    int idx = sdsSerial.readBytes(buf + 1, SDS_FRAME_LEN - 1);
    if (idx != SDS_FRAME_LEN - 1) continue; // incomplete frame, resync
    if (buf[1] != SDS_HEADER2 || buf[9] != SDS_TAIL) continue;

    uint8_t checksum = 0;
    for (int i = 2; i < 8; i++) checksum += buf[i];
    if (checksum != buf[8]) continue;

    uint16_t pm25Raw = (buf[2] | (buf[3] << 8));
    uint16_t pm10Raw = (buf[4] | (buf[5] << 8));
    pm25 = pm25Raw / 10.0f;
    pm10 = pm10Raw / 10.0f;
    return true;
  }
  return false;
}

String isoTimestamp() {
  struct tm timeinfo;
  if (getLocalTime(&timeinfo)) {
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    return String(buf);
  }
  // Fallback: still send something monotonic to avoid null
  return String("1970-01-01T00:00:00Z");
}

bool postReading(float radiation, float pm25, float tempC, float hum, float press, float voc) {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  Serial.printf("WiFi RSSI: %d dBm, free heap: %u\n", WiFi.RSSI(), ESP.getFreeHeap());
  Serial.printf("Current epoch: %ld\n", static_cast<long>(time(nullptr)));
  bool timeOk = ensureTimeSynced();
  IPAddress resolvedIp;
  if (!checkInternetReachable(resolvedIp)) {
    Serial.println("Internet check failed; skipping POST");
    return false;
  }
  if (!timeOk) {
    Serial.println("Time not synced; skipping POST");
    return false;
  }

  StaticJsonDocument<512> doc;
  doc["device_id"] = NODE_ID;
  doc["timestamp"] = timeOk ? static_cast<long>(time(nullptr)) : 0;
  JsonObject data = doc.createNestedObject("data");
  data["radiation_cpm"] = radiation;
  data["pm25"] = pm25;
  data["air_temp_c"] = tempC;
  data["humidity"] = hum;
  data["pressure_hpa"] = press;
  data["voc"] = voc;

  String body;
  serializeJson(doc, body);
  Serial.print("SENDING JSON: ");
  Serial.println(body);

  const int maxAttempts = 4;
  int backoffMs = 1000;
  for (int attempt = 1; attempt <= maxAttempts; attempt++) {
    if (WiFi.status() != WL_CONNECTED) connectWiFi();
    WiFiClientSecure client;
    client.setTimeout(15000);
    client.setInsecure(); // DEBUG ONLY - TODO: pin server cert
    HTTPClient http;
    http.setTimeout(15000);

    if (!beginHttps(http, client)) {
      Serial.println("HTTP begin failed");
      return false;
    }
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-API-Key", API_KEY);

    Serial.printf("POST attempt %d/%d\n", attempt, maxAttempts);
    int code = http.POST(body);
    String resp = http.getString();
    http.end();

    Serial.printf("POST %s -> %d\n", SERVER_URL, code);
    if (code >= 200 && code < 300) {
      Serial.println(resp);
      return true;
    } else if (code > 0) {
      Serial.printf("Server error HTTP %d\n", code);
      Serial.println(resp);
      // fall through and retry
    }
    Serial.printf("HTTP POST failed: %s\n", http.errorToString(code).c_str());
    if (attempt < maxAttempts) {
      delay(backoffMs);
      backoffMs = min(backoffMs * 2, 8000);
    }
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(200);
  bootMs = millis();
  sdsWarmupUntil = bootMs + SDS_WARMUP_MS;
  sdsDebugWindowEnd = sdsWarmupUntil + SDS_RAW_DEBUG_WINDOW_MS;
  sdsNoFrameHintAt = sdsWarmupUntil + SDS_NO_FRAME_HINT_GRACE_MS;
  geigerWindowStart = bootMs;

  if (BME680_CS_PIN >= 0) {
    pinMode(BME680_CS_PIN, OUTPUT);
    digitalWrite(BME680_CS_PIN, HIGH);
  }

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  delay(BME_I2C_STABILIZE_MS); // allow bus/sensors to power up
  i2cScan();

  pinMode(GEIGER_PIN, GEIGER_USE_PULLUP ? INPUT_PULLUP : INPUT);
  attachInterrupt(digitalPinToInterrupt(GEIGER_PIN), onGeigerPulse, RISING);

  connectWiFi();
  setupTime();

  sdsSerial.begin(9600, SERIAL_8N1, SDS_RX_PIN, SDS_TX_PIN);
  sdsSerial.setTimeout(SDS_READ_TIMEOUT_MS);
  while (sdsSerial.available()) sdsSerial.read(); // flush stale boot garbage

  if (!initBME()) {
    Serial.println("BME680 init failed; continuing without real readings.");
    bmeRetryAt = millis() + 10000;
  } else {
    delay(BME_POST_CONFIG_DELAY_MS); // stabilize before first real reading
    bmeWarmupUntil = millis();
    bmeReady = true;
  }
}

void loop() {
  rawSdsDebugWindow();

  if (!bmeReady && millis() >= bmeRetryAt) {
    Serial.println("Retrying BME680 init...");
    i2cScan();
    bmeReady = initBME();
    if (!bmeReady) {
      bmeRetryAt = millis() + 10000; // retry in 10s
    } else {
      delay(BME_POST_CONFIG_DELAY_MS);
      
      bmeWarmupUntil = millis();
    }
  }

  float tempC = lastTempC, hum = lastHum, press = lastPress, gas = lastGas;
  if (bmeReady && millis() >= bmeWarmupUntil && readBME(tempC, hum, press, gas)) {
    lastTempC = tempC;
    lastHum = hum;
    lastPress = press;
    lastGas = gas;
  } else if (bmeReady && millis() < bmeWarmupUntil) {
    Serial.println("BME680 warming up...");
  } else {
    Serial.println("Using fallback BME defaults this cycle.");
  }

  float pm25 = lastPm25;
  float pm10 = lastPm10;
  float pmRead25 = 0, pmRead10 = 0;
  bool sdsWarming = millis() < sdsWarmupUntil;
  if (readSDS(pmRead25, pmRead10)) {
    pm25 = pmRead25;
    pm10 = pmRead10;
    lastPm25 = pm25;
    lastPm10 = pm10;
    sdsLastGoodFrameMs = millis();
    sdsNoFrameHintAt = millis() + SDS_NO_FRAME_HINT_GRACE_MS;
    sdsHintShown = false;
  } else {
    if (sdsWarming) {
      Serial.println("SDS011 warming up...");
    } else if (!sdsHintShown && millis() > sdsNoFrameHintAt) {
      Serial.println("No valid SDS frames: check 5V power/fan, RX/TX swap, shared GND, or baud");
      sdsHintShown = true;
    } else if (!sdsWarming && millis() > sdsWarmupUntil) {
      Serial.println("SDS011 read failed; reusing last PM2.5 value.");
    } else {
      // still within grace window; stay quiet
    }
  }

  // Geiger CPM -> uSv/h (SEN0463)
  float radiationUsvh = lastRadiationUsvh;
  unsigned long now = millis();
  if (now - geigerWindowStart >= GEIGER_WINDOW_MS) {
    uint32_t pulses = 0;
    portENTER_CRITICAL(&geigerMux);
    pulses = geigerPulses;
    geigerPulses = 0;
    portEXIT_CRITICAL(&geigerMux);

    float cpm = (pulses * 60000.0f) / GEIGER_WINDOW_MS;
    radiationUsvh = cpm / GEIGER_CPM_PER_USVH;
    lastRadiationUsvh = radiationUsvh;
    geigerWindowStart = now;
  }
  float voc = gasToVoc(gas);

  postReading(radiationUsvh, pm25, tempC, hum, press, voc);
  delay(SEND_INTERVAL_MS);
}

void rawSdsDebugWindow() {
  if (!SDS_RAW_DEBUG) return;
  if (millis() < sdsWarmupUntil) return;
  if (millis() > sdsDebugWindowEnd) return;
  while (sdsSerial.available()) {
    uint8_t b = sdsSerial.read();
    Serial.printf("%02X ", b);
  }
}
