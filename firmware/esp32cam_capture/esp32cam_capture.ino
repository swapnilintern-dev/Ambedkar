/*
  ESP32-CAM digitization station  (board: "AI Thinker ESP32-CAM")
  GET /capture -> turns on the flash, takes a UXGA JPEG, returns it (image/jpeg)
  GET /status  -> JSON health check
  The backend calls /capture, then runs OCR and indexing.
*/
#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>

const char* WIFI_SSID = "YOUR_HOTSPOT_NAME";
const char* WIFI_PASS = "YOUR_HOTSPOT_PASSWORD";

// AI Thinker ESP32-CAM pin map
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22
#define FLASH_LED 4

WebServer server(80);

bool initCamera() {
  camera_config_t c;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (psramFound()) {
    c.frame_size = FRAMESIZE_UXGA;   // 1600x1200 - best for OCR
    c.jpeg_quality = 10;
    c.fb_count = 2;
    c.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    c.frame_size = FRAMESIZE_SVGA;
    c.jpeg_quality = 12;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_DRAM;
  }
  if (esp_camera_init(&c) != ESP_OK) return false;
  sensor_t* s = esp_camera_sensor_get();
  s->set_sharpness(s, 2);
  s->set_contrast(s, 1);
  return true;
}

void handleCapture() {
  digitalWrite(FLASH_LED, HIGH);
  delay(250);                                   // let exposure settle under the flash
  for (int i = 0; i < 2; i++) {                 // drop stale frames
    camera_fb_t* old = esp_camera_fb_get();
    if (old) esp_camera_fb_return(old);
  }
  camera_fb_t* fb = esp_camera_fb_get();
  digitalWrite(FLASH_LED, LOW);
  if (!fb) {
    server.send(500, "text/plain", "Camera capture failed");
    return;
  }
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.client().write(fb->buf, fb->len);
  size_t len = fb->len;
  esp_camera_fb_return(fb);
  Serial.printf("Captured %u bytes\n", (unsigned)len);
}

void handleStatus() {
  String json = "{\"device\":\"esp32-cam\",\"ip\":\"" + WiFi.localIP().toString() +
                "\",\"psram\":" + (psramFound() ? "true" : "false") + ",\"rssi\":" + WiFi.RSSI() + "}";
  server.send(200, "application/json", json);
}

void setup() {
  Serial.begin(115200);
  pinMode(FLASH_LED, OUTPUT);
  digitalWrite(FLASH_LED, LOW);
  if (!initCamera()) {
    Serial.println("Camera init failed - check the ribbon cable and power (5V, 2A)");
    delay(3000);
    ESP.restart();
  }
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  WiFi.setSleep(false);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println();
  Serial.print("ESP32-CAM ready. Enter this in the admin page: http://");
  Serial.println(WiFi.localIP());
  server.on("/capture", handleCapture);
  server.on("/status", handleStatus);
  server.begin();
}

void loop() {
  server.handleClient();
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); delay(1000); }
}
