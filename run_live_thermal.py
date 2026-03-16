"""
run_live_thermal.py
===================
DreamVision — One-Click Live Thermal Camera Launcher

  • AUTO DETECTS the ESP32 at ESP32_HOST:ESP32_PORT
  • Falls back to the SIMULATOR backend if ESP32 is unreachable
  • Renders a full-colour heatmap in an OpenCV window
  • Overlays real-time stats (min / avg / max °C, FPS, hotspot location)
  • Classifies product status (NORMAL / WARNING / CRITICAL / FAILURE)
    using thresholds from the local SQLite component database
  • Auto-uploads every reading to MongoDB Atlas in the background
  • Press 's' to snapshot, 'q' / Ctrl-C to quit

Usage:
    python run_live_thermal.py               # auto-detect
    python run_live_thermal.py --simulator   # force simulator
    python run_live_thermal.py --esp32       # force ESP32 (hard-fail if unreachable)
"""

import argparse
import logging
import os
import queue
import socket
import sys
import threading
import time
import uuid
from datetime import datetime

import cv2
import numpy as np

# ── path fix so we can import the camera package ─────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import camera.config as cfg
from camera.camera_interface import build_camera, SimulatorCamera, ESP32Camera
from camera.image_processing import ThermalProcessor

# ── Database & Cloud ──────────────────────────────────────────────────────────
from database.db import init_db, fetch_rule, get_connection, insert_inspection
from database.mongo_db import get_mongo_client, get_inspections_collection

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_live_thermal")

# ── Status colours (BGR) ──────────────────────────────────────────────────────
STATUS_COLORS = {
    "OK":       (50, 200, 80),    # green
    "WARNING":  (0, 180, 255),    # orange
    "NOK":      (0, 0, 220),      # red
    "UNKNOWN":  (160, 160, 160),  # grey
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _esp32_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Quick TCP probe – returns True if ESP32 is listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _classify_status(max_temp: float, rule: dict | None) -> str:
    """
    Compare live max temperature against the DB rule thresholds.
    Default thresholds used when no rule is found in the DB.
    """
    if rule is None:
        # Fallback defaults matching cfg constants
        n_min, n_max = cfg.TEMP_SAFE - 30, cfg.TEMP_SAFE
        crit           = cfg.TEMP_WARNING
        fail           = cfg.TEMP_DANGER
    else:
        n_min  = rule.get("normal_temp_min", 0)
        n_max  = rule.get("normal_temp_max", cfg.TEMP_SAFE)
        crit   = rule.get("critical_temp",   cfg.TEMP_WARNING)
        fail   = rule.get("failure_temp",    cfg.TEMP_DANGER)

    if max_temp >= crit:
        return "NOK"
    elif max_temp >= n_max:
        return "WARNING"
    else:
        return "OK"


def _detect_component(t_max: float, all_rules: list[dict]) -> tuple[str, dict | None]:
    """Auto-detects the component whose normal temperature range is closest to t_max."""
    if not all_rules:
        return "ambient", None
        
    if t_max < 30.0:  # Room temperature cutoff
        return "ambient", None

    best_match = None
    min_dist = float('inf')

    for rule in all_rules:
        # Center of its normal operating temperature
        n_min = rule.get("normal_temp_min", 0)
        n_max = rule.get("normal_temp_max", n_min + 50)
        center = (n_min + n_max) / 2.0
        
        dist = abs(t_max - center)
        if dist < min_dist:
            min_dist = dist
            best_match = rule
            
    if best_match and min_dist < 150: # Must be somewhat within range
        return best_match["component_name"], best_match
        
    return "ambient", None

def _fetch_all_components() -> list[dict]:
    """Return all rows from component_temperature_rules for the selector UI."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM component_temperature_rules ORDER BY component_name")
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _draw_overlay(frame_bgr: np.ndarray, stats: dict, fps: float,
                  backend: str, status: str, component: str,
                  rule: dict | None) -> np.ndarray:
    """Burn stats + STATUS banner onto the heatmap frame."""
    h, w = frame_bgr.shape[:2]
    bar_h   = 36
    panel_h = 65     # top status banner height
    color   = STATUS_COLORS.get(status, STATUS_COLORS["UNKNOWN"])
    overlay = frame_bgr.copy()

    # ── Bottom stats bar ─────────────────────────────────────────────────────
    cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame_bgr, 0.45, 0, frame_bgr)

    def put(text, x, y, scale=0.52, col=(255, 255, 255), thick=1):
        cv2.putText(frame_bgr, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, col, thick, cv2.LINE_AA)

    t_min = stats.get("min_celsius", 0)
    t_avg = stats.get("mean_celsius", 0)
    t_max = stats.get("max_celsius", 0)
    hx    = stats.get("hotspot_x", -1)
    hy    = stats.get("hotspot_y", -1)

    put(f"MIN {t_min:.1f}°C", 6,   h - bar_h + 14, col=(100, 200, 255))
    put(f"AVG {t_avg:.1f}°C", 110, h - bar_h + 14, col=(255, 220, 100))
    put(f"MAX {t_max:.1f}°C", 220, h - bar_h + 14, col=(100, 120, 255))
    if hx >= 0:
        put(f"HOT ({hx},{hy})", 340, h - bar_h + 14, col=(50, 50, 255))

    put(f"FPS {fps:.1f}", 6, h - bar_h + 28, scale=0.42, col=(180, 255, 180))
    put(f"[{backend}]", w - 100, h - bar_h + 28, scale=0.42, col=(200, 200, 200))

    # ── Top-right live dot + time ─────────────────────────────────────────────
    now = datetime.now().strftime("%H:%M:%S")
    put(f"● LIVE  {now}", w - 175, 20, scale=0.50, col=(0, 255, 100))

    # ── Top STATUS banner ─────────────────────────────────────────────────────
    # Semi-transparent background strip
    banner = frame_bgr.copy()
    cv2.rectangle(banner, (0, 0), (w, panel_h), (20, 20, 20), -1)
    cv2.addWeighted(banner, 0.65, frame_bgr, 0.35, 0, frame_bgr)

    # Coloured status pill
    pill_w = 160
    cv2.rectangle(frame_bgr, (8, 6), (8 + pill_w, panel_h - 6), color, -1)
    cv2.rectangle(frame_bgr, (8, 6), (8 + pill_w, panel_h - 6), (255,255,255), 1)

    put(f"  {status}", 14, 30, scale=0.68, col=(255, 255, 255), thick=2)

    # Component + threshold info on the right of the pill
    comp_label = f"Component: {component}"
    put(comp_label, 180, 18, scale=0.45, col=(210, 210, 210))

    if rule:
        thresh_txt = (f"NORM {rule['normal_temp_min']:.0f}-{rule['normal_temp_max']:.0f}°C  "
                      f"CRIT {rule['critical_temp']:.0f}°C  "
                      f"FAIL {rule['failure_temp']:.0f}°C")
        put(thresh_txt, 180, 35, scale=0.38, col=(170, 170, 170))
        
        extra_txt = f"Mat: {rule.get('material', 'N/A')} | Sens: {rule.get('sensor_type', 'N/A')}"
        put(extra_txt, 180, 50, scale=0.35, col=(150, 200, 150))
    else:
        put("No DB rule — using defaults", 180, 35, scale=0.38, col=(120, 120, 120))

    return frame_bgr


def _colormap_frame(celsius: np.ndarray) -> np.ndarray:
    """Convert float32 Celsius array → BGR heatmap (640×480)."""
    t_min, t_max = np.percentile(celsius, 2), np.percentile(celsius, 98)
    t_range = max(t_max - t_min, 1.0)
    norm = np.clip((celsius - t_min) / t_range * 255, 0, 255).astype(np.uint8)
    colored = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
    resized = cv2.resize(colored, (cfg.DISPLAY_WIDTH, cfg.DISPLAY_HEIGHT),
                         interpolation=cv2.INTER_LINEAR)
    return resized


def _build_stats(celsius: np.ndarray) -> dict:
    amin  = float(np.min(celsius))
    amax  = float(np.max(celsius))
    amean = float(np.mean(celsius))
    hy, hx = np.unravel_index(np.argmax(celsius), celsius.shape)
    return {
        "min_celsius":  round(amin,  2),
        "mean_celsius": round(amean, 2),
        "max_celsius":  round(amax,  2),
        "hotspot_x":    int(hx),
        "hotspot_y":    int(hy),
    }


# ── Cloud Upload (background thread) ─────────────────────────────────────────

class _CloudUploader(threading.Thread):
    """
    Drains an internal queue and upserts records to MongoDB Atlas.
    Runs as a daemon so it dies cleanly when the main process exits.
    """

    def __init__(self):
        super().__init__(daemon=True, name="cloud-uploader")
        self._q: queue.Queue = queue.Queue(maxsize=50)
        self._mongo_col = None
        self._ready = False

    def connect(self):
        """Call once before starting the thread."""
        try:
            client = get_mongo_client()
            if client:
                self._mongo_col = get_inspections_collection(client)
                self._ready = True
                logger.info("[Cloud] ✓ Connected to MongoDB Atlas.")
            else:
                logger.warning("[Cloud] MongoDB unavailable – uploads disabled.")
        except Exception as exc:
            logger.warning("[Cloud] MongoDB connect error: %s", exc)

    def enqueue(self, record: dict):
        """Non-blocking put – drops the oldest record if the queue is full."""
        try:
            self._q.put_nowait(record)
        except queue.Full:
            try:
                self._q.get_nowait()   # discard oldest
            except queue.Empty:
                pass
            self._q.put_nowait(record)

    def run(self):
        while True:
            try:
                record = self._q.get(timeout=2)
            except queue.Empty:
                continue
            if self._ready and self._mongo_col is not None:
                try:
                    self._mongo_col.update_one(
                        {"part_uid": record["part_uid"]},
                        {"$set": record},
                        upsert=True,
                    )
                    logger.debug("[Cloud] Uploaded %s", record["part_uid"])
                except Exception as exc:
                    logger.warning("[Cloud] Upload failed: %s", exc)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DreamVision Live Thermal Camera")
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--simulator", action="store_true",
                          help="Force simulator mode (no hardware needed)")
    mode_grp.add_argument("--esp32",    action="store_true",
                          help="Force ESP32 mode (fail if unreachable)")
    parser.add_argument("--component",  default="crankcase",
                        help="Component name for DB threshold lookup (default: crankcase)")
    args = parser.parse_args()

    # ── DB init ───────────────────────────────────────────────────────────────
    try:
        init_db()
    except Exception as exc:
        logger.warning("DB init error: %s — status classification uses defaults.", exc)

    component_name = args.component
    all_rules = _fetch_all_components()
    logger.info("Loaded %d component rules from database for auto-detection.", len(all_rules))

    # ── Cloud uploader ────────────────────────────────────────────────────────
    uploader = _CloudUploader()
    uploader.connect()
    uploader.start()

    # ── Choose backend ────────────────────────────────────────────────────────
    if args.simulator:
        backend_name = "SIMULATOR"
        camera = SimulatorCamera()
        logger.info("Forced SIMULATOR mode.")
    elif args.esp32:
        backend_name = "ESP32"
        camera = ESP32Camera()
        logger.info("Forced ESP32 mode – will fail if %s:%d is unreachable.",
                    cfg.ESP32_HOST, cfg.ESP32_PORT)
    else:
        logger.info("Auto-detecting backend … probing ESP32 at %s:%d",
                    cfg.ESP32_HOST, cfg.ESP32_PORT)
        if _esp32_reachable(cfg.ESP32_HOST, cfg.ESP32_PORT):
            backend_name = "ESP32"
            camera = ESP32Camera()
            logger.info("✓ ESP32 reachable — using real hardware stream.")
        else:
            backend_name = "SIMULATOR"
            camera = SimulatorCamera()
            logger.warning("✗ ESP32 not reachable — falling back to SIMULATOR.")

    # ── Open camera ───────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  DreamVision  ●  Live Thermal Feed")
    print(f"  Backend   : {backend_name}")
    print(f"  Component : AUTO-DETECT (Based on Temp)")
    print(f"  DB Rules  : {len(all_rules)} loaded")
    print(f"  Cloud     : {'MongoDB Atlas' if uploader._ready else 'Offline'}")
    print(f"  Press 'q' to quit  |  's' to snapshot")
    print("═" * 60 + "\n")

    try:
        camera.open()
    except Exception as exc:
        logger.critical("Failed to open camera: %s", exc)
        sys.exit(1)

    # ── OpenCV window ─────────────────────────────────────────────────────────
    WIN = "DreamVision  ●  Live Thermal Feed"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, cfg.DISPLAY_WIDTH, cfg.DISPLAY_HEIGHT + 65)  # extra for banner

    snap_dir = os.path.join(os.path.dirname(__file__), "data", "snapshots")
    os.makedirs(snap_dir, exist_ok=True)

    fps_t0      = time.time()
    fps_frames  = 0
    fps         = 0.0
    upload_every = 5          # upload one record every N frames
    frame_idx    = 0
    last_status  = "UNKNOWN"

    try:
        while True:
            celsius = camera.next_frame()
            if celsius is None:
                logger.warning("No frame – retrying …")
                continue

            fps_frames += 1
            frame_idx  += 1
            elapsed = time.time() - fps_t0
            if elapsed >= 1.0:
                fps = fps_frames / elapsed
                fps_frames = 0
                fps_t0 = time.time()

            stats   = _build_stats(celsius)
            t_max   = stats["max_celsius"]

            # ── Auto-Detect Component & Status ────────────────────────────
            current_comp, current_rule = _detect_component(t_max, all_rules)
            
            if current_comp == "ambient":
                last_status = "UNKNOWN"
            else:
                last_status = _classify_status(t_max, current_rule)

            # ── Render frame ──────────────────────────────────────────────
            heatmap = _colormap_frame(celsius)
            display = _draw_overlay(heatmap, stats, fps, backend_name,
                                    last_status, current_comp, current_rule)

            cv2.imshow(WIN, display)

            # ── Key handling ──────────────────────────────────────────────
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                logger.info("'q' pressed – quitting.")
                break
            elif key == ord('s'):
                ts_dt = datetime.now()
                ts_fn = ts_dt.strftime("%Y%m%d_%H%M%S")
                ts_iso = ts_dt.isoformat(timespec="seconds")
                
                # Save snapshot image
                filename = f"thermal_{ts_fn}_{last_status}.png"
                path = os.path.join(snap_dir, filename)
                cv2.imwrite(path, display)
                logger.info("Snapshot saved: %s", path)
                
                # Upload to DB & Cloud
                uid = f"{current_comp}_{ts_fn}_{uuid.uuid4().hex[:6]}"
                record = {
                    "part_uid":       uid,
                    "component_name": current_comp,
                    "temperature":    round(t_max, 2),
                    "status":         last_status,
                    "device_id":      cfg.DEVICE_ID,
                    "image_path":     filename,
                    "timestamp":      ts_iso,
                    "sync_status":    "SYNCED" if uploader._ready else "PENDING",
                    "stats":          stats,
                }
                
                # Local SQLite edge save
                try:
                    insert_inspection(
                        part_uid=uid,
                        component_name=current_comp,
                        temperature=round(t_max, 2),
                        status=last_status,
                        device_id=cfg.DEVICE_ID,
                        image_path=filename,
                        timestamp=ts_iso,
                        sync_status="SYNCED" if uploader._ready else "PENDING",
                    )
                    logger.info("Saved to local database: %s", uid)
                    
                    # Notify Dashboard instantly
                    def notify_dash():
                        try:
                            import requests
                            broadcast_msg = {
                                "part_uid": uid,
                                "component_name": current_comp,
                                "temperature": round(t_max, 2),
                                "status": last_status,
                                "timestamp": ts_iso
                            }
                            requests.post("http://127.0.0.1:3000/inspection", json=broadcast_msg, timeout=1)
                        except Exception:
                            pass
                    threading.Thread(target=notify_dash, daemon=True).start()
                    
                except Exception as e:
                    logger.error("Failed to save to local DB: %s", e)
                    
                # Cloud queue
                uploader.enqueue(record)
                logger.info("Queued for cloud upload: %s", uid)

    except KeyboardInterrupt:
        logger.info("Interrupted – shutting down.")
    finally:
        camera.close()
        cv2.destroyAllWindows()
        logger.info("Done.")


if __name__ == "__main__":
    main()
