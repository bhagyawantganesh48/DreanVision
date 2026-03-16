/*
 * DreamVision ESP32 Thermal Firmware
 * ─────────────────────────────────
 * MLX90640 32×24 thermal camera → WiFi TCP stream
 * Optional: ST7789 TFT display shows a live heatmap
 *
 * Wiring (I²C for MLX90640):
 *   SDA → GPIO 21
 *   SCL → GPIO 22
 *   VCC → 3.3 V
 *   GND → GND
 *
 * Wiring (SPI for ST7789 TFT, optional):
 *   CS  → GPIO 5
 *   DC  → GPIO 2
 *   RST → GPIO 4
 *   SCK → GPIO 18
 *   SDA → GPIO 23
 */

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>

// ── MLX90640 ──────────────────────────────────────────────────────────────────
#include <Adafruit_MLX90640.h>

// ── TFT display (comment out if no screen attached) ─────────────────────────
#define USE_TFT_DISPLAY  // Remove this line to disable the TFT
#ifdef USE_TFT_DISPLAY
  #include <Adafruit_GFX.h>
  #include <Adafruit_ST7789.h>
  #define TFT_CS   5
  #define TFT_DC   2
  #define TFT_RST  4
  Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_RST);
#endif

// ── WiFi credentials ─────────────────────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_SSID";       // <-- change me
const char* WIFI_PASSWORD = "YOUR_PASSWORD";   // <-- change me

// ── TCP server ────────────────────────────────────────────────────────────────
const uint16_t TCP_PORT = 5000;
WiFiServer   server(TCP_PORT);
WiFiClient   client;

// ── MLX90640 ─────────────────────────────────────────────────────────────────
Adafruit_MLX90640 mlx;
float frame[32 * 24];  // 768 floats

// ── Helpers ──────────────────────────────────────────────────────────────────

// Color Mapping Function - dynamic temperature-based colors
uint16_t tempToColor(float t, float minT=20, float maxT=50) {
  int val = constrain((int)((t-minT)*255.0f/(maxT-minT)), 0, 255);
  if (val < 85) return tft.color565(val*3, 0, 255-val*3);      // Blue
  if (val < 170) return tft.color565(255, (val-85)*3, 0);     // Green→Yellow
  return tft.color565(255, 255-(val-170)*3, 0);               // Red
}

// Bilinear Interpolation
void interpolate(float *raw, uint16_t *display, float minT, float maxT, int srcW=32, int srcH=24, int dstW=320, int dstH=240) {
  for (int y=0; y<dstH; y++) {
    int sy = (y * srcH) / dstH;
    int sy2 = (sy+1 >= srcH) ? sy : sy+1;
    float ay = ((float)y * srcH / dstH) - sy;
    for (int x=0; x<dstW; x++) {
      int sx = (x * srcW) / dstW;
      int sx2 = (sx+1 >= srcW) ? sx : sx+1;
      float ax = ((float)x * srcW / dstW) - sx;
      
      // Bilinear interpolation
      float t1 = raw[sy*srcW+sx] * (1-ax) + raw[sy*srcW+sx2] * ax;
      float t2 = raw[sy2*srcW+sx] * (1-ax) + raw[sy2*srcW+sx2] * ax;
      float temp = t1 * (1-ay) + t2 * ay;
      
      display[y*dstW + x] = tempToColor(temp, minT, maxT);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[DreamVision] Booting ESP32 Thermal Firmware...");

  // ── I²C for MLX90640
  Wire.begin(21, 22);
  Wire.setClock(400000);  // 400 kHz fast mode

  if (!mlx.begin(MLX90640_I2CADDR_DEFAULT, &Wire)) {
    Serial.println("[ERROR] MLX90640 not found – check wiring!");
    while (1) delay(1000);
  }
  Serial.println("[OK] MLX90640 initialised.");
  mlx.setMode(MLX90640_CHESS);
  mlx.setResolution(MLX90640_ADC_18BIT);
  mlx.setRefreshRate(MLX90640_16_HZ);

  // ── TFT display
#ifdef USE_TFT_DISPLAY
  tft.init(240, 320);
  tft.setRotation(1);
  tft.fillScreen(ST77XX_BLACK);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(2);
  tft.setCursor(10, 10);
  tft.print("DreamVision");
  Serial.println("[OK] ST7789 TFT initialised.");
#endif

  // ── WiFi
  Serial.printf("[WiFi] Connecting to %s ...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] Connected – IP: %s\n", WiFi.localIP().toString().c_str());

  // ── TCP server
  server.begin();
  Serial.printf("[TCP]  Listening on port %u\n", TCP_PORT);
}

void loop() {
  // Accept new client if none is connected
  if (!client || !client.connected()) {
    client = server.accept();
    if (client) {
      Serial.printf("[TCP]  Client connected: %s\n",
                    client.remoteIP().toString().c_str());
    }
  }

  // Read one thermal frame
  if (mlx.getFrame(frame) != 0) {
    Serial.println("[WARN] Failed to get MLX90640 frame – retrying");
    return;
  }

  // ── Send frame over TCP as raw binary (768 × 4 bytes = 3072 bytes) ────────
  if (client && client.connected()) {
    // Header: magic + frame size so the Python reader can re-sync
    uint8_t header[8];
    header[0] = 0xAA; header[1] = 0xBB;   // magic bytes
    uint32_t len = 768 * sizeof(float);
    memcpy(header + 2, &len, 4);
    header[6] = 0x00; header[7] = 0x00;   // reserved
    client.write(header, sizeof(header));
    client.write((uint8_t*)frame, len);
    client.flush();
  }

  // ── Optional TFT heatmap ──────────────────────────────────────────────────
#ifdef USE_TFT_DISPLAY
  // Find min/max for colour scaling
  float tMin = frame[0], tMax = frame[0];
  for (int i = 1; i < 768; i++) {
    tMin = min(tMin, frame[i]);
    tMax = max(tMax, frame[i]);
  }
  
  // Allocate buffer for 320x240 image
  // Note: 320x240 * 2 bytes = 153.6 KB, which fits in ESP32 heap.
  uint16_t* display_buf = (uint16_t*) malloc(320 * 240 * sizeof(uint16_t));
  if (display_buf != NULL) {
    interpolate(frame, display_buf, tMin, tMax, 32, 24, 320, 240);
    // Push entire image to screen at once using Adafruit_GFX drawRGBBitmap
    tft.drawRGBBitmap(0, 0, display_buf, 320, 240);
    free(display_buf);
  } else {
    Serial.println("[WARN] Failed to allocate display buffer!");
  }
  
  // Print temperature range overlay
  tft.fillRect(0, 0, 320, 20, ST77XX_BLACK);
  tft.setCursor(4, 2);
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_WHITE);
  tft.printf("Min: %.1f C   Max: %.1f C", tMin, tMax);
#endif
}
