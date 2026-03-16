"""
capture/config.py
=================
Central configuration for the DreamVision Phase-1 Thermal Capture Module.

All tuneable parameters live here so the rest of the codebase never hard-codes
values.  Edit this file to adapt the system to a different hardware target or
deployment environment.
"""

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Device identity
# ---------------------------------------------------------------------------
DEVICE_ID = os.environ.get("DREAMVISION_DEVICE_ID", "DreamVision-01")

# ---------------------------------------------------------------------------
# Camera back-end selection
# "MLX90640"   – real I²C thermal sensor  (adafruit CircuitPython library)
# "FLIR_LEPTON"– real SPI/I²C Lepton 2/3  (pylepton / pigpio library)
# "ESP32"      – Waveshare / generic ESP32 TCP stream (80x62 raw uint16 LE)
# "SIMULATOR"  – pure-NumPy synthetic hotspot generator (no hardware needed)
# ---------------------------------------------------------------------------
CAMERA_BACKEND = os.environ.get("DREAMVISION_CAMERA_BACKEND", "ESP32")

# ---------------------------------------------------------------------------
# ESP32 / TCP stream settings  (used only when CAMERA_BACKEND == "ESP32")
# ---------------------------------------------------------------------------
ESP32_HOST       = os.environ.get("ESP32_HOST", "192.168.4.1")
ESP32_PORT       = int(os.environ.get("ESP32_PORT", "3333"))
ESP32_TIMEOUT_S  = 10          # socket connect / read timeout (seconds)
ESP32_RECV_BUF   = 131_072     # 128 KB kernel receive buffer

# Frame wire-protocol constants (Waveshare MLX90640 over ESP32)
TCP_FRAME_SIZE   = 10_256
STRIP_HEAD       = 332         # bytes to discard at start of each TCP frame
STRIP_TAIL       = 4           # bytes to discard at end (CRC "XXXX")
RAW_DATA_SIZE    = 9_920       # 80 × 62 × 2
ESP32_WIDTH      = 80
ESP32_HEIGHT     = 62

# ---------------------------------------------------------------------------
# MLX90640 native I²C settings  (used only when CAMERA_BACKEND == "MLX90640")
# ---------------------------------------------------------------------------
MLX_WIDTH        = 32
MLX_HEIGHT       = 24
MLX_REFRESH_RATE = 4           # Hz  — valid: 0.5, 1, 2, 4, 8, 16, 32, 64

# ---------------------------------------------------------------------------
# FLIR Lepton settings  (used only when CAMERA_BACKEND == "FLIR_LEPTON")
# ---------------------------------------------------------------------------
LEPTON_WIDTH     = 80
LEPTON_HEIGHT    = 60
LEPTON_SPI_PORT  = 0
LEPTON_SPI_SPEED = 16_000_000  # 16 MHz

# ---------------------------------------------------------------------------
# RGB / Pi Camera  (optional second camera)
# ---------------------------------------------------------------------------
RGB_ENABLED           = os.environ.get("DREAMVISION_RGB_ENABLED", "false").lower() == "true"
RGB_CAMERA_INDEX      = int(os.environ.get("DREAMVISION_RGB_INDEX", "0"))
RGB_CAPTURE_WIDTH     = 640
RGB_CAPTURE_HEIGHT    = 480

# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------
# OpenCV colormap applied to the normalised 8-bit thermal frame
COLORMAP           = "COLORMAP_INFERNO"   # see cv2.COLORMAP_* constants

# Overrides for live-display window size (0 = use native sensor resolution)
DISPLAY_WIDTH      = 640
DISPLAY_HEIGHT     = 480

# Temperature thresholds (°C) used for on-frame annotation
TEMP_SAFE          = 60.0
TEMP_WARNING       = 90.0
TEMP_DANGER        = 120.0
TEMP_FIRE_RISK     = 150.0

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
DATA_ROOT          = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
THERMAL_IMAGE_DIR  = os.path.join(DATA_ROOT, "thermal_images")
RGB_IMAGE_DIR      = os.path.join(DATA_ROOT, "rgb_images")
LOG_DIR            = os.path.join(DATA_ROOT, "logs")
METADATA_LOG_FILE  = os.path.join(LOG_DIR, "metadata.jsonl")   # newline-delimited JSON

# How often to auto-save a frame even without an explicit capture command
#  0 = only save on explicit user request (press 's' or API call)
AUTO_SAVE_INTERVAL = 0          # disabled by default to save storage

# ---------------------------------------------------------------------------
# Performance / capture
# ---------------------------------------------------------------------------
TARGET_FPS         = 5          # minimum guaranteed capture rate
LOOP_SLEEP_S       = 1.0 / TARGET_FPS

# Raw thermal → Celsius conversion for ESP32/Waveshare stream
# Formula reverse-engineered from Waveshare firmware:  T = raw * 0.0984 − 265.82
RAW_TO_CELSIUS_SCALE  = 0.0984
RAW_TO_CELSIUS_OFFSET = -265.82
TEMP_MIN_CLIP         = -20.0   # °C
TEMP_MAX_CLIP         = 300.0   # °C  (red-hot parts can exceed 150 °C)

# ---------------------------------------------------------------------------
# FastAPI local server  (optional — run main_api.py to enable)
# ---------------------------------------------------------------------------
API_HOST   = "0.0.0.0"
API_PORT   = 8001
API_RELOAD = False
