/*
  ESP8266 (NodeMCU) display-case security node
  Wire loop: D5 -> jumper wire taped across the case lid -> GND
  Loop intact = case closed (LOW). Wire pulled = case opened (HIGH, internal pull-up).
  Sends tamper_open / tamper_closed events instantly and a heartbeat every 30 s.
*/
#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClient.h>

const char* WIFI_SSID = "YOUR_HOTSPOT_NAME";
const char* WIFI_PASS = "YOUR_HOTSPOT_PASSWORD";
const char* BACKEND   = "http://192.168.43.100:8000/api/sensor/event";  // laptop IP printed by app.py
const char* TOKEN     = "sih-demo-token";                              // must match SENSOR_TOKEN in .env
const char* NODE_NAME = "manuscript-case-1";

const int LOOP_PIN = D5;
int stableState = -1, lastRead = -1;
unsigned long lastChange = 0, lastBeat = 0, lastBlink = 0;

void postEvent(const char* type, const char* value) {
  if (WiFi.status() != WL_CONNECTED) return;
  WiFiClient client;
  HTTPClient http;
  http.begin(client, BACKEND);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Sensor-Token", TOKEN);
  String body = String("{\"type\":\"") + type + "\",\"source\":\"" + NODE_NAME + "\",\"value\":\"" + value + "\"}";
  int code = http.POST(body);
  Serial.printf("%s -> HTTP %d\n", type, code);
  http.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(LOOP_PIN, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);              // LED is active-low: HIGH = off
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.print("\nConnected, IP ");
  Serial.println(WiFi.localIP());
}

void loop() {
  int r = digitalRead(LOOP_PIN);
  if (r != lastRead) { lastRead = r; lastChange = millis(); }
  if (millis() - lastChange > 60 && r != stableState) {       // debounce
    stableState = r;
    bool open = (stableState == HIGH);
    postEvent(open ? "tamper_open" : "tamper_closed", open ? "open" : "closed");
  }
  if (stableState == HIGH && millis() - lastBlink > 200) {   // blink while open
    lastBlink = millis();
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
  } else if (stableState == LOW) {
    digitalWrite(LED_BUILTIN, HIGH);
  }
  if (millis() - lastBeat > 30000) {
    lastBeat = millis();
    postEvent("heartbeat", stableState == HIGH ? "open" : "closed");
  }
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); delay(500); }
}
