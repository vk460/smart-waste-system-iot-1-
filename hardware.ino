// ==========================================
// ESP32 SMART RECYCLING MACHINE
// ==========================================
// Django is the only component that creates a RecyclingSession/session_id.
// Configure SERVER_URL with the development computer's LAN IP, not
// 127.0.0.1 or localhost. Example: http://192.168.1.100:8000
//
// Existing Django machine API flow:
// RFID scan -> READY_FOR_DEPOSIT
// weight    -> MEASURING
// processing -> PROCESSING
// complete  -> COMPLETED
//
// The ESP32 sends weight in grams because Django stores weight_grams and the
// existing API reads data.weight_g. Django calculates and awards the points.

// ==========================================
// 1. LIBRARIES
// ==========================================
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <HX711.h>
#include <MFRC522.h>
#include <SPI.h>
#include <WiFi.h>

// ==========================================
// 2. WIFI CONFIGURATION
// ==========================================
const char *WIFI_SSID = "YOUR_WIFI_SSID";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

const unsigned long WIFI_CONNECT_TIMEOUT_MS = 20000;
const unsigned long WIFI_RETRY_INTERVAL_MS = 5000;
unsigned long lastWiFiAttempt = 0;

// ==========================================
// 3. DJANGO SERVER CONFIGURATION
// ==========================================
const char *SERVER_URL = "http://192.168.1.100:8000";

// ==========================================
// 4. MACHINE AUTHENTICATION
// ==========================================
// This must match Machine.code and Machine.api_key in Django.
const char *MACHINE_ID = "MACHINE_001";
const char *MACHINE_API_KEY = "CHANGE_ME_MACHINE_API_KEY";

// The existing backend accepts the key in X-Machine-API-Key or JSON api_key.
// This sketch sends the header and JSON field for compatibility.

// ==========================================
// 5. HARDWARE PINS
// ==========================================
// The previous repository example used ESP8266 pins. These are ESP32 pins.
// Change them to match the physical wiring before uploading.
#define RFID_SS_PIN 5
#define RFID_RST_PIN 27
#define RFID_SCK_PIN 18
#define RFID_MOSI_PIN 23
#define RFID_MISO_PIN 19

#define HX711_DT_PIN 32
#define HX711_SCK_PIN 33

// ==========================================
// 6. SENSOR CONFIGURATION
// ==========================================
// Replace this with the value obtained by calibrating the actual load cell.
float CALIBRATION_FACTOR = 2280.0f;
const float MAX_WEIGHT_GRAMS = 500.0f;
const float MIN_WEIGHT_GRAMS = 0.5f;
const uint8_t STABLE_SAMPLES = 6;
const float STABILITY_TOLERANCE_GRAMS = 2.0f;

// Django's weight endpoint accepts one valid weight while the session is
// READY_FOR_DEPOSIT. It rejects later weight posts after MEASURING, so the
// machine sends one stable reading rather than repeatedly posting updates.

// ==========================================
// 7. SESSION STATE
// ==========================================
MFRC522 rfid(RFID_SS_PIN, RFID_RST_PIN);
HX711 scale;
String activeSessionId;

// ==========================================
// 8. WIFI HELPERS
// ==========================================
void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  unsigned long now = millis();
  if (now - lastWiFiAttempt < WIFI_RETRY_INTERVAL_MS) return;
  lastWiFiAttempt = now;

  Serial.println("[WIFI] Connecting...");
  WiFi.disconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WIFI] Connected. IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("[ERROR] Wi-Fi connection timed out");
  }
}

// ==========================================
// 9. API HELPERS
// ==========================================
bool postJson(const char *path, JsonDocument &request, DynamicJsonDocument &response, int &httpStatus) {
  ensureWiFi();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[ERROR] No Wi-Fi connection");
    return false;
  }

  HTTPClient http;
  String url = String(SERVER_URL) + path;
  if (!http.begin(url)) {
    Serial.println("[ERROR] Could not create HTTP request");
    return false;
  }

  http.setTimeout(10000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Machine-API-Key", MACHINE_API_KEY);

  String body;
  serializeJson(request, body);
  Serial.print("[API] POST ");
  Serial.println(path);

  httpStatus = http.POST(body);
  String rawResponse = http.getString();
  Serial.print("[API] HTTP ");
  Serial.println(httpStatus);

  if (httpStatus < 200 || httpStatus >= 300) {
    Serial.print("[ERROR] Django response: ");
    Serial.println(rawResponse);
    http.end();
    return false;
  }

  if (rawResponse.length() == 0) {
    Serial.println("[ERROR] Empty Django response");
    http.end();
    return false;
  }

  DeserializationError error = deserializeJson(response, rawResponse);
  if (error) {
    Serial.print("[ERROR] Invalid JSON response: ");
    Serial.println(error.c_str());
    http.end();
    return false;
  }

  http.end();
  return true;
}

void addMachineCredentials(JsonDocument &request) {
  request["device_id"] = MACHINE_ID;
  request["api_key"] = MACHINE_API_KEY;
}

// ==========================================
// 10. RFID FUNCTIONS
// ==========================================
String readMifareText(byte block) {
  MFRC522::MIFARE_Key key;
  for (byte index = 0; index < 6; index++) key.keyByte[index] = 0xFF;

  byte buffer[18];
  byte size = sizeof(buffer);
  MFRC522::StatusCode authStatus = rfid.PCD_Authenticate(
      MFRC522::PICC_CMD_MF_AUTH_KEY_A,
      block,
      &key,
      &(rfid.uid));
  if (authStatus != MFRC522::STATUS_OK) return "";
  if (rfid.MIFARE_Read(block, buffer, &size) != MFRC522::STATUS_OK) return "";

  buffer[16] = 0;
  String value = String((char *)buffer);
  value.trim();
  return value;
}

String readRfidUid() {
  String uid;
  for (byte index = 0; index < rfid.uid.size; index++) {
    if (rfid.uid.uidByte[index] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[index], HEX);
    if (index + 1 < rfid.uid.size) uid += ":";
  }
  uid.toUpperCase();
  return uid;
}

bool verifyRfidAndCreateSession() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return false;

  String uid = readRfidUid();
  Serial.print("[RFID] UID: ");
  Serial.println(uid);

  // These blocks match the existing hardware_api_example.ino convention.
  String cardId = readMifareText(4);
  String name = readMifareText(5);
  String phone = readMifareText(7);

  DynamicJsonDocument request(768);
  addMachineCredentials(request);
  request["event"] = "rfid_detected";
  request["rfid_uid"] = uid;
  if (cardId.length()) request["card_id"] = cardId;
  if (name.length()) request["name"] = name;
  if (phone.length()) request["phone"] = phone;

  DynamicJsonDocument response(2048);
  int httpStatus = 0;
  bool success = postJson("/api/machines/rfid-scan/", request, response, httpStatus);

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  if (!success) {
    if (httpStatus == 401) Serial.println("[ERROR] Machine authentication failed");
    else if (httpStatus == 403) Serial.println("[ERROR] RFID card/user is not authorized");
    else if (httpStatus == 409) Serial.println("[ERROR] RFID conflict or active-card conflict");
    return false;
  }

  const char *returnedSessionId = response["session_id"] | "";
  const char *returnedStatus = response["status"] | "";
  if (strlen(returnedSessionId) == 0) {
    Serial.println("[ERROR] RFID response did not contain a Django session_id");
    return false;
  }

  activeSessionId = String(returnedSessionId);
  Serial.print("[SESSION] Django session ID: ");
  Serial.println(activeSessionId);
  Serial.print("[SESSION] Status: ");
  Serial.println(returnedStatus);
  return true;
}

// ==========================================
// 11. WEIGHT FUNCTIONS
// ==========================================
float readStableWeightGrams() {
  if (!scale.is_ready()) {
    Serial.println("[ERROR] HX711 is not ready");
    return -1.0f;
  }

  float readings[STABLE_SAMPLES];
  float minimum = 1000000.0f;
  float maximum = -1000000.0f;
  float total = 0.0f;

  for (uint8_t index = 0; index < STABLE_SAMPLES; index++) {
    float reading = scale.get_units(1);
    readings[index] = reading;
    total += reading;
    if (reading < minimum) minimum = reading;
    if (reading > maximum) maximum = reading;
    delay(80);
  }

  if (maximum - minimum > STABILITY_TOLERANCE_GRAMS) {
    Serial.println("[ERROR] Weight reading is unstable");
    return -1.0f;
  }

  float weight = total / STABLE_SAMPLES;
  if (weight < 0) weight = 0;
  return weight;
}

bool sendWeightUpdate() {
  float weightGrams = readStableWeightGrams();
  if (weightGrams < MIN_WEIGHT_GRAMS || weightGrams > MAX_WEIGHT_GRAMS) {
    Serial.print("[ERROR] Invalid weight in grams: ");
    Serial.println(weightGrams);
    return false;
  }

  Serial.print("[WEIGHT] ");
  Serial.print(weightGrams, 2);
  Serial.println(" g");

  DynamicJsonDocument request(512);
  addMachineCredentials(request);
  request["session_id"] = activeSessionId;
  request["event"] = "weight_stable";
  JsonObject data = request.createNestedObject("data");
  data["weight_g"] = weightGrams;

  DynamicJsonDocument response(1536);
  int httpStatus = 0;
  if (!postJson("/api/machines/weight/", request, response, httpStatus)) return false;

  const char *status = response["status"] | "";
  Serial.print("[SESSION] Weight accepted; Django status: ");
  Serial.println(status);
  return strcmp(status, "MEASURING") == 0;
}

bool sendProcessing() {
  DynamicJsonDocument request(512);
  addMachineCredentials(request);
  request["session_id"] = activeSessionId;
  request["event"] = "processing";

  DynamicJsonDocument response(1536);
  int httpStatus = 0;
  if (!postJson("/api/machines/processing/", request, response, httpStatus)) return false;

  const char *status = response["status"] | "";
  Serial.print("[SESSION] Processing confirmed; Django status: ");
  Serial.println(status);
  return strcmp(status, "PROCESSING") == 0;
}

bool completeRecyclingSession() {
  DynamicJsonDocument request(512);
  addMachineCredentials(request);
  request["session_id"] = activeSessionId;
  request["event"] = "deposit_completed";

  DynamicJsonDocument response(1536);
  int httpStatus = 0;
  if (!postJson("/api/machines/session-complete/", request, response, httpStatus)) return false;

  const char *status = response["status"] | "";
  Serial.print("[SESSION] Completion status: ");
  Serial.println(status);
  if (strcmp(status, "COMPLETED") != 0) return false;

  Serial.print("[SESSION] Points awarded by Django: ");
  Serial.println(response["points"] | 0);
  Serial.println("[SESSION] Recycling completed successfully");
  return true;
}

// ==========================================
// 12. RECYCLING STATE MACHINE
// ==========================================
void runRecyclingAttempt() {
  if (!verifyRfidAndCreateSession()) return;

  // Only Django generated activeSessionId. No ID is generated here.
  if (!sendWeightUpdate()) {
    Serial.println("[ERROR] Weight was not accepted; session remains server-controlled");
    activeSessionId = "";
    return;
  }

  if (!sendProcessing()) {
    Serial.println("[ERROR] Processing transition failed");
    activeSessionId = "";
    return;
  }

  if (!completeRecyclingSession()) {
    Serial.println("[ERROR] Session completion failed");
    activeSessionId = "";
    return;
  }

  activeSessionId = "";
}

// ==========================================
// 13. SETUP AND MAIN LOOP
// ==========================================
void setup() {
  Serial.begin(115200);
  delay(200);

  WiFi.mode(WIFI_STA);
  ensureWiFi();

  SPI.begin(RFID_SCK_PIN, RFID_MISO_PIN, RFID_MOSI_PIN, RFID_SS_PIN);
  rfid.PCD_Init();

  scale.begin(HX711_DT_PIN, HX711_SCK_PIN);
  scale.set_scale(CALIBRATION_FACTOR);
  scale.tare();

  Serial.println("[MACHINE] ESP32 recycling machine ready");
  Serial.println("[MACHINE] Tap an RFID card to begin");
}

void loop() {
  ensureWiFi();

  if (activeSessionId.length() == 0) {
    runRecyclingAttempt();
  }

  delay(100);
}
