"""
capture/camera_interface.py
============================
Hardware abstraction layer for thermal (and optional RGB) cameras.

Supported back-ends (controlled via config.CAMERA_BACKEND):
  • SIMULATOR    – synthetic NumPy frames; no hardware required
  • ESP32        – Waveshare / generic ESP32 TCP raw uint16 LE stream
  • MLX90640     – direct I²C via the adafruit-circuitpython-mlx90640 library
  • FLIR_LEPTON  – direct SPI via the pylepton library

Each backend exposes the same interface:
    camera = build_camera()
    camera.open()
    frame_celsius = camera.next_frame()   # np.ndarray float32, shape=(H, W)
    camera.close()
"""

import abc
import logging
import time
import socket
import queue
import threading
from typing import Optional

import numpy as np

import camera.config as cfg

logger = logging.getLogger("dreamvision.camera")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class ThermalCamera(abc.ABC):
    """Common interface all camera back-ends must implement."""

    def __init__(self):
        self._open = False

    @abc.abstractmethod
    def open(self) -> None:
        """Initialise hardware / sockets.  Must be called before next_frame()."""

    @abc.abstractmethod
    def next_frame(self) -> Optional[np.ndarray]:
        """
        Return a float32 NumPy array of shape (H, W) containing temperature
        in degrees Celsius, or None if no frame is available yet.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Release all resources cleanly."""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# SIMULATOR backend
# ---------------------------------------------------------------------------

class SimulatorCamera(ThermalCamera):
    """
    Pure-software synthetic thermal camera.
    Generates a realistic 64×64 thermal scene with a moving hotspot.
    No hardware required – ideal for development and CI.
    """

    WIDTH  = 64
    HEIGHT = 64

    def __init__(self):
        super().__init__()
        self._frame_count = 0
        self._t0 = time.time()

    def open(self) -> None:
        logger.info("[SIMULATOR] Synthetic thermal camera initialised (%dx%d).",
                    self.WIDTH, self.HEIGHT)
        self._open = True

    def next_frame(self) -> np.ndarray:
        elapsed = time.time() - self._t0
        # Oscillate hotspot temperature to simulate a machine warming up/cooling
        peak_temp = 55.0 + 35.0 * abs(np.sin(elapsed * 0.3))

        # Base ambient background  (Gaussian noise around 35 °C)
        rng   = np.random.default_rng(int(elapsed * 10))
        frame = rng.normal(loc=35.0, scale=3.0, size=(self.HEIGHT, self.WIDTH)).astype(np.float32)

        # Radial hotspot centred at a slowly drifting position
        cx = int(self.WIDTH  / 2 + 10 * np.sin(elapsed * 0.5))
        cy = int(self.HEIGHT / 2 + 8  * np.cos(elapsed * 0.4))
        cy = max(8, min(cy, self.HEIGHT - 8))
        cx = max(8, min(cx, self.WIDTH  - 8))

        yy, xx = np.ogrid[:self.HEIGHT, :self.WIDTH]
        dist   = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2).astype(np.float32)
        spread = 10.0
        hotspot = np.exp(-(dist ** 2) / (2.0 * spread ** 2)) * peak_temp
        frame  += hotspot

        self._frame_count += 1
        time.sleep(cfg.LOOP_SLEEP_S)
        return frame

    def close(self) -> None:
        logger.info("[SIMULATOR] Shut down after %d frames.", self._frame_count)
        self._open = False


# ---------------------------------------------------------------------------
# ESP32 / Waveshare TCP stream backend
# ---------------------------------------------------------------------------

class ESP32Camera(ThermalCamera):
    """
    Reads raw uint16-LE frames from a Waveshare / generic ESP32 thermal camera
    over a persistent TCP connection.

    Frame wire format (10 256 bytes):
      [0   – 11 ] : 12-byte ASCII header  '   #2808GFRA'   (skip)
      [12  – 171] : 160 bytes zeros / padding                 (skip)
      [172 – 331] : 160 bytes row-0 metadata                  (skip)
      [332 –10251]: 9920 bytes = 80 × 62 × uint16 LE thermal pixels
      [10252–10255]: 4-byte CRC  'XXXX'                        (skip)

    The socket reader runs in a background thread and pushes frames into a
    queue so the capture loop never blocks on I/O.
    """

    def __init__(self):
        super().__init__()
        self._sock: Optional[socket.socket] = None
        self._frame_q: queue.Queue = queue.Queue(maxsize=2)
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_wreg_cmd(addr: int, val: int) -> bytes:
        payload = f"000CWREG{addr:02X}{val:02X}".encode()
        crc = sum(payload) & 0xFFFF
        return b"   #" + payload + f"{crc:04X}".encode()

    START_CMD = None  # built lazily after class body

    def _connect(self) -> socket.socket:
        """Attempt TCP connection; block + retry until successful."""
        while not self._stop_event.is_set():
            sock = None
            try:
                logger.info("[ESP32] Connecting to %s:%d …", cfg.ESP32_HOST, cfg.ESP32_PORT)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, cfg.ESP32_RECV_BUF)
                sock.settimeout(cfg.ESP32_TIMEOUT_S)
                sock.connect((cfg.ESP32_HOST, cfg.ESP32_PORT))
                # Send start-stream command
                sock.sendall(self._make_wreg_cmd(0xB1, 0x03))
                # Discard WREG ACK
                sock.settimeout(1)
                try:
                    sock.recv(64)
                except socket.timeout:
                    pass
                sock.settimeout(cfg.ESP32_TIMEOUT_S)
                logger.info("[ESP32] Connected. Stream started.")
                return sock
            except Exception as exc:
                logger.warning("[ESP32] Connection failed: %s. Retrying in 3 s …", exc)
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                time.sleep(3)
        return None  # stop_event set

    def _recv_exact(self, sock: socket.socket, n: int) -> Optional[bytes]:
        """Read exactly n bytes from sock; return None on disconnect/timeout."""
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except (socket.timeout, OSError) as exc:
                logger.warning("[ESP32] Socket read error: %s", exc)
                return None
            if not chunk:
                logger.warning("[ESP32] Connection closed by peer.")
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _reader_loop(self) -> None:
        """Background thread: drain TCP stream and push decoded frames into the queue."""
        while not self._stop_event.is_set():
            self._sock = self._connect()
            if self._sock is None:
                break
            logger.info("[ESP32] Reader thread started.")
            while not self._stop_event.is_set():
                raw = self._recv_exact(self._sock, cfg.TCP_FRAME_SIZE)
                if raw is None:
                    logger.warning("[ESP32] Frame read failed (None returned) – reconnecting …")
                    break
                
                # logger.debug(f"[ESP32] Received {len(raw)} bytes.") # Too noisy
                if self._frame_q.empty():
                     # Only log once in a while to avoid spam
                     if int(time.time()) % 5 == 0:
                         logger.info(f"[ESP32] Frame received ({len(raw)} bytes)")
                # Slice the pixel payload
                pixel_bytes = raw[cfg.STRIP_HEAD: cfg.TCP_FRAME_SIZE - cfg.STRIP_TAIL]
                if len(pixel_bytes) != cfg.RAW_DATA_SIZE:
                    logger.warning("[ESP32] Unexpected payload size %d; skipping frame.", len(pixel_bytes))
                    continue
                # Decode uint16 → float32 Celsius
                raw_u16 = np.frombuffer(pixel_bytes, dtype=np.uint16).reshape(
                    cfg.ESP32_HEIGHT, cfg.ESP32_WIDTH)
                celsius = (raw_u16.astype(np.float32) * cfg.RAW_TO_CELSIUS_SCALE
                           + cfg.RAW_TO_CELSIUS_OFFSET)
                celsius = np.clip(celsius, cfg.TEMP_MIN_CLIP, cfg.TEMP_MAX_CLIP)
                try:
                    self._frame_q.get_nowait()  # drop old frame to keep queue from stalling
                except queue.Empty:
                    pass
                self._frame_q.put(celsius)
            try:
                self._sock.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def open(self) -> None:
        logger.info("[ESP32] Starting camera reader for %s:%d", cfg.ESP32_HOST, cfg.ESP32_PORT)
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True,
                                               name="esp32-reader")
        self._reader_thread.start()
        
        # Wait for the first frame to arrive to confirm connection, or timeout
        start_wait = time.time()
        timeout = 15.0
        while time.time() - start_wait < timeout:
            if not self._frame_q.empty():
                self._open = True
                logger.info("[ESP32] Connection confirmed - first frame received.")
                return
            if time.time() - start_wait > 5.0 and (int(time.time() * 10) % 20 == 0):
                logger.info(f"[ESP32] Still waiting for first frame... ({time.time() - start_wait:.1f}s)")
            time.sleep(0.1)
        
        # If we get here, connection failed or timed out
        self.close()
        raise ConnectionError(f"Failed to connect to ESP32 at {cfg.ESP32_HOST}:{cfg.ESP32_PORT} within {timeout} seconds (no frames received).")

    def next_frame(self) -> Optional[np.ndarray]:
        try:
            frame = self._frame_q.get(timeout=cfg.ESP32_TIMEOUT_S)
            return frame
        except queue.Empty:
            logger.warning("[ESP32] No frame received within %d s.", cfg.ESP32_TIMEOUT_S)
            return None

    def close(self) -> None:
        logger.info("[ESP32] Shutting down …")
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._reader_thread:
            self._reader_thread.join(timeout=5)
        self._open = False
        logger.info("[ESP32] Camera closed.")


# ---------------------------------------------------------------------------
# MLX90640 I²C backend
# ---------------------------------------------------------------------------

class MLX90640Camera(ThermalCamera):
    """
    Adafruit / CircuitPython driver for the MLX90640 32×24 I²C thermal sensor.
    Requires: pip install adafruit-circuitpython-mlx90640
    """

    def __init__(self):
        super().__init__()
        self._mlx = None

    def open(self) -> None:
        try:
            import board
            import busio
            import adafruit_mlx90640
        except ImportError as e:
            raise RuntimeError(
                "MLX90640 library not installed. Run: "
                "pip install adafruit-circuitpython-mlx90640"
            ) from e

        i2c = busio.I2C(board.SCL, board.SDA, frequency=400_000)
        self._mlx = adafruit_mlx90640.MLX90640(i2c)
        self._mlx.refresh_rate = getattr(
            adafruit_mlx90640.RefreshRate,
            f"REFRESH_{cfg.MLX_REFRESH_RATE}HZ",
            adafruit_mlx90640.RefreshRate.REFRESH_4_HZ,
        )
        logger.info("[MLX90640] Sensor initialised (%dx%d @ %d Hz).",
                    cfg.MLX_WIDTH, cfg.MLX_HEIGHT, cfg.MLX_REFRESH_RATE)
        self._open = True

    def next_frame(self) -> Optional[np.ndarray]:
        frame_buf = [0.0] * (cfg.MLX_WIDTH * cfg.MLX_HEIGHT)
        try:
            self._mlx.getFrame(frame_buf)
        except Exception as exc:
            logger.error("[MLX90640] getFrame() failed: %s", exc)
            return None
        arr = np.array(frame_buf, dtype=np.float32).reshape(cfg.MLX_HEIGHT, cfg.MLX_WIDTH)
        return arr

    def close(self) -> None:
        logger.info("[MLX90640] Camera closed.")
        self._open = False


# ---------------------------------------------------------------------------
# FLIR Lepton backend
# ---------------------------------------------------------------------------

class FLIRLeptonCamera(ThermalCamera):
    """
    FLIR Lepton 2.x / 3.x reader via the pylepton library.
    Requires: pip install pylepton
    """

    def __init__(self):
        super().__init__()
        self._capture = None

    def open(self) -> None:
        try:
            from pylepton import camera
        except ImportError as e:
            raise RuntimeError(
                "pylepton not installed. Run: pip install pylepton"
            ) from e
        self._Capture = Capture
        logger.info("[FLIR Lepton] Sensor initialised (%dx%d).",
                    cfg.LEPTON_WIDTH, cfg.LEPTON_HEIGHT)
        self._open = True

    def next_frame(self) -> Optional[np.ndarray]:
        try:
            with self._Capture("/dev/spidev0.0") as cap:
                arr, _ = cap.get_frame()
            # Lepton raw values → Kelvin × 100; convert to °C
            celsius = arr.astype(np.float32) / 100.0 - 273.15
            return celsius
        except Exception as exc:
            logger.error("[FLIR Lepton] Frame capture error: %s", exc)
            return None

    def close(self) -> None:
        logger.info("[FLIR Lepton] Camera closed.")
        self._open = False


# ---------------------------------------------------------------------------
# RGB camera (optional second camera)
# ---------------------------------------------------------------------------

class RGBCamera:
    """
    Optional visible-spectrum camera captured via OpenCV VideoCapture.
    Returns BGR numpy array or None on failure.
    """

    def __init__(self, index: int = cfg.RGB_CAMERA_INDEX):
        self._index = index
        self._cap   = None

    def open(self) -> None:
        import cv2
        self._cap = cv2.VideoCapture(self._index)
        if not self._cap.isOpened():
            logger.warning("[RGB] Could not open camera at index %d.", self._index)
        else:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  cfg.RGB_CAPTURE_WIDTH)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.RGB_CAPTURE_HEIGHT)
            logger.info("[RGB] Camera opened (index %d, %dx%d).",
                        self._index, cfg.RGB_CAPTURE_WIDTH, cfg.RGB_CAPTURE_HEIGHT)

    def read(self):
        if self._cap is None or not self._cap.isOpened():
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def close(self) -> None:
        if self._cap:
            self._cap.release()
            logger.info("[RGB] Camera released.")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def build_camera() -> ThermalCamera:
    """
    Instantiate and return the correct ThermalCamera for the configured backend.
    """
    backend = cfg.CAMERA_BACKEND.upper()
    mapping = {
        "SIMULATOR":   SimulatorCamera,
        "ESP32":       ESP32Camera,
        "MLX90640":    MLX90640Camera,
        "FLIR_LEPTON": FLIRLeptonCamera,
    }
    if backend not in mapping:
        raise ValueError(
            f"Unknown CAMERA_BACKEND '{cfg.CAMERA_BACKEND}'. "
            f"Choose from: {list(mapping)}"
        )
    logger.info("Building camera backend: %s", backend)
    return mapping[backend]()
