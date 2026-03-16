"""
capture/image_processing.py
============================
Converts raw float32 thermal arrays into annotated visual frames and
extracts per-frame analytics (hotspot coordinates, temperature stats).

Public API
----------
    processor = ThermalProcessor()
    result = processor.process(frame_celsius)

    result.heatmap_bgr      – OpenCV BGR image ready for display or saving
    result.stats            – FrameStats namedtuple with max/min/avg/hotspots
    result.status           – "SAFE" | "WARNING" | "DANGER" | "FIRE RISK"
"""

import logging
from dataclasses import dataclass, field
from typing import List, Tuple

import cv2
import numpy as np

import camera.config as cfg

logger = logging.getLogger("dreamvision.processing")

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class Hotspot:
    """A detected region in the frame whose temperature exceeds the threshold."""
    x: int
    y: int
    w: int
    h: int
    area: int
    max_temp: float

    def to_dict(self):
        return {
            "x": self.x, "y": self.y,
            "w": self.w, "h": self.h,
            "area": self.area,
            "max_temp": round(self.max_temp, 2),
        }


@dataclass
class FrameStats:
    """Per-frame temperature statistics and hotspot list."""
    min_temp:  float
    max_temp:  float
    avg_temp:  float
    hotspots:  List[Hotspot] = field(default_factory=list)

    def to_dict(self):
        return {
            "min_temp":  round(self.min_temp, 2),
            "max_temp":  round(self.max_temp, 2),
            "avg_temp":  round(self.avg_temp, 2),
            "hotspots":  [h.to_dict() for h in self.hotspots],
        }


@dataclass
class ProcessedFrame:
    """All outputs of ThermalProcessor.process()."""
    heatmap_bgr: np.ndarray        # annotated BGR image (uint8)
    stats:       FrameStats
    status:      str               # SAFE / WARNING / DANGER / FIRE RISK
    status_color: Tuple[int,int,int]


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------

_STATUS_TABLE = [
    (cfg.TEMP_FIRE_RISK, "NOK",     (0,   0,   255)),
    (cfg.TEMP_DANGER,    "NOK",     (0,   80,  255)),
    (cfg.TEMP_WARNING,   "WARNING", (0,   200, 255)),
    (0.0,                "OK",      (0,   200, 80)),
]

def _classify_status(max_temp: float):
    """Return (label, BGR colour) for the given peak temperature."""
    for threshold, label, colour in _STATUS_TABLE:
        if max_temp >= threshold:
            return label, colour
    return "OK", (0, 200, 80)


# ---------------------------------------------------------------------------
# Cmap lookup
# ---------------------------------------------------------------------------

def _get_colormap_id(name: str) -> int:
    """Resolve config colour-map name to OpenCV integer constant."""
    cmap_id = getattr(cv2, name, None)
    if cmap_id is None:
        logger.warning("Unknown colormap '%s'; falling back to COLORMAP_INFERNO.", name)
        cmap_id = cv2.COLORMAP_INFERNO
    return cmap_id


# ---------------------------------------------------------------------------
# Main processor class
# ---------------------------------------------------------------------------

class ThermalProcessor:
    """
    Stateless-ish processor that converts a float32 Celsius array into a
    fully-annotated BGR heatmap, statistics, and a status classification.
    """

    # Hotspot detection parameters
    HOTSPOT_THRESH_RATIO  = 0.75   # fraction of (max − min) used as pixel threshold
    HOTSPOT_MIN_AREA      = 4      # px² – ignore specks
    HOTSPOT_MIN_CONTRAST  = 15.0   # °C above avg to count as a hotspot

    def __init__(self):
        self._cmap_id = _get_colormap_id(cfg.COLORMAP)

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def process(self, frame_celsius: np.ndarray) -> ProcessedFrame:
        """
        Full thermal processing pipeline.

        Parameters
        ----------
        frame_celsius : np.ndarray  shape (H, W)  dtype float32
            Raw temperature data in degrees Celsius.

        Returns
        -------
        ProcessedFrame
        """
        # 1. Compute statistics
        stats = self._compute_stats(frame_celsius)

        # 2. Normalise to uint8 using percentile stretching (2% to 98%)
        norm8 = self._normalise_to_uint8(frame_celsius)

        # 3. Apply CLAHE enhancement
        enhanced = self._apply_clahe(norm8)

        # 4. Apply colour map
        heatmap = cv2.applyColorMap(enhanced, self._cmap_id)

        # 5. Detect hotspots (now using the enhanced image for better selection)
        stats.hotspots = self._detect_hotspots(frame_celsius, enhanced, stats)

        # 6. Resize for display using Lanczos4 interpolation
        h_disp, w_disp = cfg.DISPLAY_HEIGHT, cfg.DISPLAY_WIDTH
        heatmap = cv2.resize(heatmap, (w_disp, h_disp), interpolation=cv2.INTER_LANCZOS4)

        # 7. Apply sharpening (Unsharp Mask)
        heatmap = self._apply_sharpening(heatmap)

        # 8. Classify status
        status, status_colour = _classify_status(stats.max_temp)

        # 9. Draw overlays
        heatmap = self._draw_overlays(heatmap, stats, status, status_colour, frame_celsius)

        return ProcessedFrame(
            heatmap_bgr=heatmap,
            stats=stats,
            status=status,
            status_color=status_colour,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_stats(frame: np.ndarray) -> FrameStats:
        return FrameStats(
            min_temp=float(np.min(frame)),
            max_temp=float(np.max(frame)),
            avg_temp=float(np.mean(frame)),
        )

    @staticmethod
    def _normalise_to_uint8(frame: np.ndarray) -> np.ndarray:
        """Stretch contrast using the 2nd and 98th percentiles."""
        vmin, vmax = np.percentile(frame, [2, 98])
        if vmax - vmin < 1e-3:
            # Fallback to absolute min/max if the frame is very flat
            vmin, vmax = frame.min(), frame.max()
            if vmax - vmin < 1e-6:
                return np.zeros(frame.shape, dtype=np.uint8)
        
        norm = np.clip(frame, vmin, vmax)
        return ((norm - vmin) / (vmax - vmin) * 255.0).astype(np.uint8)

    @staticmethod
    def _apply_clahe(img_gray: np.ndarray) -> np.ndarray:
        """Contrast Limited Adaptive Histogram Equalization."""
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
        return clahe.apply(img_gray)

    @staticmethod
    def _apply_sharpening(img: np.ndarray) -> np.ndarray:
        """Sharpen using an Unsharp Mask (Gaussian blur subtraction)."""
        blurred = cv2.GaussianBlur(img, (0, 0), 3)
        return cv2.addWeighted(img, 1.5, blurred, -0.5, 0)

    def _detect_hotspots(self, frame: np.ndarray, norm_img: np.ndarray, stats: FrameStats) -> List[Hotspot]:
        """Contour-based hotspot detection on the normalised/enhanced 8-bit frame."""
        # Build a threshold mask
        f_min, f_max = stats.min_temp, stats.max_temp
        if f_max - f_min < 1e-6:
            return []
            
        thresh_val = int(self.HOTSPOT_THRESH_RATIO * 255)
        _, binary = cv2.threshold(norm_img, thresh_val, 255, cv2.THRESH_BINARY)

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        hotspots: List[Hotspot] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.HOTSPOT_MIN_AREA:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            roi_max = float(np.max(frame[y:y + h, x:x + w]))
            if roi_max > stats.avg_temp + self.HOTSPOT_MIN_CONTRAST:
                hotspots.append(Hotspot(x=x, y=y, w=w, h=h,
                                        area=int(area), max_temp=roi_max))
        return hotspots

    def _draw_overlays(
        self,
        img: np.ndarray,
        stats: FrameStats,
        status: str,
        status_colour: Tuple[int, int, int],
        original_frame: np.ndarray,
    ) -> np.ndarray:
        """Draw temperature annotations and status banner on the heatmap."""
        H, W = img.shape[:2]
        orig_H, orig_W = original_frame.shape[:2]
        scale_x = W / orig_W
        scale_y = H / orig_H

        # Draw hotspot rectangles (scaled from sensor to display coordinates)
        for hs in stats.hotspots:
            rx1 = int(hs.x * scale_x)
            ry1 = int(hs.y * scale_y)
            rx2 = int((hs.x + hs.w) * scale_x)
            ry2 = int((hs.y + hs.h) * scale_y)
            cv2.rectangle(img, (rx1, ry1), (rx2, ry2), (255, 255, 255), 2)
            cv2.putText(img, f"{hs.max_temp:.1f}C",
                        (rx1 + 2, max(ry1 - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        # Status banner at top
        banner_h = 32
        cv2.rectangle(img, (0, 0), (W, banner_h), status_colour, -1)
        cv2.putText(img, f"STATUS: {status}",
                    (8, banner_h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 255, 255), 2, cv2.LINE_AA)

        # Temperature stats strip at bottom
        strip_y = H - 28
        cv2.rectangle(img, (0, strip_y), (W, H), (30, 30, 30), -1)
        info = (f"  Max: {stats.max_temp:6.1f}C   Avg: {stats.avg_temp:6.1f}C"
                f"   Min: {stats.min_temp:6.1f}C   Hotspots: {len(stats.hotspots)}")
        cv2.putText(img, info, (4, H - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)

        return img
