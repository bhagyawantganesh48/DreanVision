"""
camera/live_view.py
====================
UPGRADE 1 — Live Camera Visualization Window

Displays the thermal inspection feed in an OpenCV window overlaid with:
  - Component Name
  - Temperature
  - Status (OK / WARNING / NOK)

Can run as a stand-alone module or be imported and driven by the startup
script (run_dreamvision.py).

    python -m camera.live_view
    DREAMVISION_CAMERA_BACKEND=SIMULATOR python -m camera.live_view
"""

import logging
import os
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from camera import build_camera, ThermalProcessor, setup_logging, ensure_directories
import camera.config as cfg

logger = logging.getLogger("dreamvision.live_view")

WINDOW_TITLE = "DreamVision Thermal Feed"

# Status → BGR colour mapping for the overlay badge
_STATUS_COLOURS = {
    "OK":      (50, 200, 80),    # green
    "WARNING": (30, 180, 230),   # amber/orange
    "NOK":     (50, 60,  230),   # red
    "SAFE":    (50, 200, 80),
    "DANGER":  (50, 60,  230),
    "FIRE RISK": (20, 20, 220),
    "UNKNOWN": (160, 160, 160),
}

# ──────────────────────────────────────────────────────────────────────────────
# Overlay helper
# ──────────────────────────────────────────────────────────────────────────────

def _draw_overlay(frame: np.ndarray,
                  component: str,
                  temperature: float,
                  status: str) -> np.ndarray:
    """Burn inspection metadata directly onto a copy of the frame."""
    img = frame.copy()
    h, w = img.shape[:2]

    # ── Semi-transparent dark panel at the top ────────────────────────────
    overlay_panel = img.copy()
    cv2.rectangle(overlay_panel, (0, 0), (w, 100), (15, 15, 15), -1)
    cv2.addWeighted(overlay_panel, 0.7, img, 0.3, 0, img)

    # ── Factory brand watermark ────────────────────────────────────────────
    cv2.putText(img, "DreamVision AI Inspection",
                (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)

    # ── Timestamp ─────────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cv2.putText(img, ts, (w - 230, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1, cv2.LINE_AA)

    # ── Divider line ──────────────────────────────────────────────────────
    cv2.line(img, (0, 28), (w, 28), (60, 60, 60), 1)

    # ── Main metadata lines ───────────────────────────────────────────────
    status_colour = _STATUS_COLOURS.get(status.upper(), (160, 160, 160))

    cv2.putText(img, f"Component   : {component}",
                (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 1, cv2.LINE_AA)

    cv2.putText(img, f"Temperature : {temperature:.1f}\u00b0C",
                (12, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 1, cv2.LINE_AA)

    # Status badge
    status_text = f"Status      : {status}"
    cv2.putText(img, status_text,
                (12, 98), cv2.FONT_HERSHEY_SIMPLEX, 0.65, status_colour, 2, cv2.LINE_AA)

    # ── Small status indicator circle in top-right corner ─────────────────
    cx, cy, r = w - 20, 14, 10
    cv2.circle(img, (cx, cy), r, status_colour, -1)
    cv2.circle(img, (cx, cy), r, (255, 255, 255), 1)

    # ── Bottom note ───────────────────────────────────────────────────────
    cv2.putText(img, "Press  Q  to quit",
                (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (100, 100, 100), 1, cv2.LINE_AA)

    return img


# ──────────────────────────────────────────────────────────────────────────────
# Main live-view loop
# ──────────────────────────────────────────────────────────────────────────────

class LiveView:
    """
    Manages the OpenCV thermal feed window.

    Usage:
        lv = LiveView()
        lv.start()          # start background capture + display
        ...
        lv.stop()
    """

    def __init__(self, line_id: str = "L1"):
        self.line_id  = line_id
        self._running = False
        self._thread  = None

        # Latest inspection result (shared between capture and display)
        self._lock      = threading.Lock()
        self._last_frame = None
        self._component  = "---"
        self._temperature = 0.0
        self._status     = "UNKNOWN"

    # ── Public API ────────────────────────────────────────────────────────

    def start(self):
        """Start capture loop in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._capture_loop, daemon=True, name="live-view-capture")
        self._thread.start()
        logger.info("[LiveView] Started capture thread.")

    def stop(self):
        """Signal the capture thread to stop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[LiveView] Stopped.")

    def run_display_loop(self):
        """
        Blocking display loop – call from the MAIN thread so that
        cv2.imshow works on platforms that require GUI calls on the main thread.
        Returns when the user presses 'q' or the window is closed.
        """
        logger.info(f"[LiveView] Opening window: '{WINDOW_TITLE}'")
        try:
            cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(WINDOW_TITLE, cfg.DISPLAY_WIDTH, cfg.DISPLAY_HEIGHT)
        except Exception as exc:
            logger.warning("[LiveView] Could not create window (%s). Headless?", exc)
            return

        while self._running:
            with self._lock:
                frame     = self._last_frame
                component = self._component
                temp      = self._temperature
                status    = self._status

            if frame is not None:
                display = _draw_overlay(frame, component, temp, status)
                try:
                    cv2.imshow(WINDOW_TITLE, display)
                except cv2.error:
                    break

            key = cv2.waitKey(30) & 0xFF
            if key == ord('q') or key == 27:   # q or ESC
                logger.info("[LiveView] User pressed Q — closing window.")
                self.stop()
                break

        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

    # ── Internal capture loop (background thread) ─────────────────────────

    def _capture_loop(self):
        """Continuously reads frames from the camera and processes them."""
        backoff = 2  # seconds between reconnection attempts

        while self._running:
            cam       = build_camera()
            processor = ThermalProcessor()

            try:
                logger.info("[LiveView] Opening %s camera …", cfg.CAMERA_BACKEND)
                cam.open()
            except Exception as exc:
                logger.error("[LiveView] Camera open failed: %s. Retrying in %ds …",
                             exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue

            backoff = 2   # reset on successful open
            logger.info("[LiveView] Camera connected — capturing frames.")

            consecutive_failures = 0
            MAX_FAIL = 20

            while self._running:
                try:
                    raw = cam.next_frame()
                except Exception as exc:
                    consecutive_failures += 1
                    logger.warning("[LiveView] Frame error (%d/%d): %s",
                                   consecutive_failures, MAX_FAIL, exc)
                    if consecutive_failures >= MAX_FAIL:
                        logger.error("[LiveView] Too many frame errors. Reconnecting …")
                        break
                    time.sleep(0.1)
                    continue

                if raw is None:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_FAIL:
                        logger.warning("[LiveView] Camera appears disconnected. Reconnecting …")
                        break
                    continue

                consecutive_failures = 0

                try:
                    result = processor.process(raw)
                except Exception as exc:
                    logger.error("[LiveView] Processing error: %s", exc)
                    continue

                # Extract stats for the overlay
                temp = result.stats.max_temp if hasattr(result.stats, "max_temp") else 0.0
                status  = result.status or "UNKNOWN"
                component = cfg.DEVICE_ID  # update live when pipeline feeds back real component

                with self._lock:
                    self._last_frame  = result.heatmap_bgr.copy()
                    self._temperature = temp
                    self._status      = status
                    self._component   = component

            # Camera loop exited; close device and maybe retry
            try:
                cam.close()
            except Exception:
                pass

            if self._running:
                logger.info("[LiveView] Reconnecting in %ds …", backoff)
                time.sleep(backoff)

    # ── Allow external code to push an inspection result directly ─────────

    def update_inspection(self, component: str, temperature: float, status: str):
        """
        Called by the inspection pipeline so the overlay reflects the
        AI decision even when the frame itself came from raw capture.
        """
        with self._lock:
            self._component   = component
            self._temperature = temperature
            self._status      = status


# ──────────────────────────────────────────────────────────────────────────────
# Stand-alone entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup_logging()
    ensure_directories()

    lv = LiveView()
    lv.start()

    try:
        lv.run_display_loop()   # blocks until 'q'
    except KeyboardInterrupt:
        logger.info("[LiveView] Interrupted.")
    finally:
        lv.stop()
