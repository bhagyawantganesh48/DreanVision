"""
capture/data_storage.py
========================
Handles all file I/O for the DreamVision Phase-1 capture module.

Responsibilities
----------------
  • Create canonical directory structure under data/
  • Save thermal heatmap PNGs with timestamped filenames
  • Save optional RGB images
  • Append per-frame metadata records to a newline-delimited JSON log
  • Provide an atomic frame counter so IDs are never repeated across restarts

Directory layout created and maintained by this module:

    data/
    ├── thermal_images/
    │   ├── frame_0001_20260315T142310.png
    │   └── …
    ├── rgb_images/
    │   ├── frame_0001_20260315T142310.png
    │   └── …
    └── logs/
        ├── metadata.jsonl          (machine-readable per-frame records)
        └── dreamvision.log         (human-readable runtime log)
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

import camera.config as cfg

logger = logging.getLogger("dreamvision.storage")


# ---------------------------------------------------------------------------
# Directory bootstrap
# ---------------------------------------------------------------------------

def ensure_directories() -> None:
    """Create the full data directory tree if it does not already exist."""
    for d in (cfg.THERMAL_IMAGE_DIR, cfg.RGB_IMAGE_DIR, cfg.LOG_DIR):
        os.makedirs(d, exist_ok=True)
    logger.info("Storage directories verified: %s", cfg.DATA_ROOT)


# ---------------------------------------------------------------------------
# Atomic frame counter
# ---------------------------------------------------------------------------

_COUNTER_LOCK = threading.Lock()
_counter_file = os.path.join(cfg.LOG_DIR, ".frame_counter")


def _read_counter() -> int:
    """Read persisted counter, initialising to 0 if missing."""
    try:
        with open(_counter_file, "r") as fh:
            return int(fh.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_counter(value: int) -> None:
    """Persist counter atomically (write then rename)."""
    tmp = _counter_file + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(str(value))
    os.replace(tmp, _counter_file)


def next_frame_id() -> str:
    """
    Return the next zero-padded frame ID string (e.g. "FRAME_0042")
    and atomically increment the persistent counter.
    """
    with _COUNTER_LOCK:
        val = _read_counter()
        _write_counter(val + 1)
    return f"FRAME_{val + 1:04d}"


# ---------------------------------------------------------------------------
# Core save functions
# ---------------------------------------------------------------------------

def save_thermal_image(
    heatmap_bgr: np.ndarray,
    frame_id: str,
    timestamp: datetime,
) -> str:
    """
    Save a thermal heatmap to the thermal_images directory.

    Returns
    -------
    str
        Relative path from the project root (for metadata logging).
    """
    ts_str   = timestamp.strftime("%Y%m%dT%H%M%S")
    filename = f"{frame_id.lower()}_{ts_str}.png"
    abs_path = os.path.join(cfg.THERMAL_IMAGE_DIR, filename)
    rel_path = os.path.relpath(abs_path, start=os.path.dirname(cfg.DATA_ROOT))

    try:
        success = cv2.imwrite(abs_path, heatmap_bgr)
        if not success:
            raise IOError(f"cv2.imwrite returned False for {abs_path}")
        logger.debug("Thermal image saved: %s", abs_path)
    except Exception as exc:
        logger.error("Failed to save thermal image %s: %s", abs_path, exc)
        raise

    return rel_path


def save_rgb_image(
    rgb_bgr: np.ndarray,
    frame_id: str,
    timestamp: datetime,
) -> str:
    """
    Save an RGB (visible spectrum) image to the rgb_images directory.

    Returns
    -------
    str
        Relative path from the project root.
    """
    ts_str   = timestamp.strftime("%Y%m%dT%H%M%S")
    filename = f"{frame_id.lower()}_{ts_str}_rgb.png"
    abs_path = os.path.join(cfg.RGB_IMAGE_DIR, filename)
    rel_path = os.path.relpath(abs_path, start=os.path.dirname(cfg.DATA_ROOT))

    try:
        success = cv2.imwrite(abs_path, rgb_bgr)
        if not success:
            raise IOError(f"cv2.imwrite returned False for {abs_path}")
        logger.debug("RGB image saved: %s", abs_path)
    except Exception as exc:
        logger.error("Failed to save RGB image %s: %s", abs_path, exc)
        raise

    return rel_path


# ---------------------------------------------------------------------------
# Metadata logging
# ---------------------------------------------------------------------------

_META_LOCK = threading.Lock()


def append_metadata(
    frame_id: str,
    timestamp: datetime,
    stats_dict: dict,
    image_path: str,
    status: str,
    rgb_image_path: Optional[str] = None,
) -> dict:
    """
    Build a metadata record and append it as a JSON line to metadata.jsonl.

    Returns
    -------
    dict
        The complete metadata record (useful for API responses or tests).
    """
    record = {
        "timestamp":     timestamp.isoformat(timespec="seconds"),
        "device_id":     cfg.DEVICE_ID,
        "frame_id":      frame_id,
        "status":        status,
        "max_temp":      stats_dict.get("max_temp"),
        "avg_temp":      stats_dict.get("avg_temp"),
        "min_temp":      stats_dict.get("min_temp"),
        "hotspot_count": len(stats_dict.get("hotspots", [])),
        "image_path":    image_path,
        "rgb_image_path": rgb_image_path,
    }
    _write_metadata_line(record)
    logger.info("Metadata logged: %s | Status: %s | MaxT: %.1f°C",
                frame_id, status, stats_dict.get("max_temp", 0.0))
    return record


def _write_metadata_line(record: dict) -> None:
    """Thread-safe append of a JSON line to the metadata log file."""
    ensure_directories()
    line = json.dumps(record) + "\n"
    with _META_LOCK:
        try:
            with open(cfg.METADATA_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as exc:
            logger.error("Metadata write error: %s", exc)


def read_metadata_all() -> list:
    """
    Read and parse all metadata records from the JSONL log.

    Returns
    -------
    list[dict]
        All records, newest last.  Returns [] if file is missing.
    """
    if not os.path.exists(cfg.METADATA_LOG_FILE):
        return []
    records = []
    with open(cfg.METADATA_LOG_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("Skipped malformed metadata line: %s", exc)
    return records
