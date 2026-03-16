import cv2
import requests
import numpy as np
import time
import base64
import logging
import camera.config as cfg

logger = logging.getLogger("dreamvision.esp32_stream")

class ESP32ThermalStream:
    def __init__(self, endpoints=None):
        if endpoints is None:
            # Prioritize the host in config.py, followed by common defaults
            self.endpoints = [f"http://{cfg.ESP32_HOST}"]
            defaults = [
                "http://192.168.4.1", 
                "http://192.168.0.100", 
                "http://192.168.1.100",
                "http://localhost:8000"
            ]
            for d in defaults:
                if d not in self.endpoints:
                    self.endpoints.append(d)
        else:
            self.endpoints = endpoints
            
        self.stream_url = None
        self.is_connected = False
        self.cap = None
        
    def connect(self):
        """Discovers and connects to the ESP32 Thermal MJPEG stream."""
        logger.info("Discovering ESP32 thermal camera...")
        for ip in self.endpoints:
            # MJPEG stream patterns
            urls_to_try = [f"{ip}:81/stream", f"{ip}/stream", f"{ip}/capture"]
            
            for url in urls_to_try:
                try:
                    res = requests.get(url, stream=True, timeout=1.0)
                    if res.status_code == 200:
                        self.stream_url = url
                        self.is_connected = True
                        logger.info(f"Connected to ESP32 MJPEG Stream at: {url}")
                        
                        self.cap = cv2.VideoCapture(url)
                        if self.cap.isOpened():
                            return True
                except requests.RequestException:
                    continue

        self.is_connected = False
        logger.warning("No ESP32 Stream detected on known endpoints.")
        return False
        
    def read_frame(self):
        """Reads a frame from the stream and returns raw CV2 float matrix format."""
        if not self.is_connected or self.cap is None:
            return False, None
            
        ret, frame = self.cap.read()
        if not ret:
            # Reconnect Logic
            logger.warning("Stream dropped. Reconnecting...")
            time.sleep(1)
            self.connect()
            return False, None
            
        # Convert RGB to intensity/grayscale to map as thermal data
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Approximate thermal mapping (simulate heat map from 0-255 pixels)
        # Assuming the ESP32 streams an MJPEG. For actual radiometric, we'd parse MLX packets.
        thermal_float = gray.astype(np.float32)
        
        # Scale range dynamically to approx 20C-160C range to fit DreamVision rules
        thermal_float = (thermal_float / 255.0) * 140.0 + 20.0
        return True, thermal_float

    def get_base64_frame(self, temp_override=None):
        """Grabs a frame and generates the expected Base64 PNG payload for the Edge API."""
        ret, frame = self.read_frame()
        if not ret:
            # If no actual camera stream, generate a fallback "Simulated" frame to prevent pipeline crash
            frame = np.zeros((64, 64), dtype=np.float32) + 25.0
            if temp_override:
                cv2.circle(frame, (32, 32), 10, temp_override, -1)
                
        _, buf = cv2.imencode(".png", frame.astype(np.uint8))
        return base64.b64encode(buf).decode("utf-8")
        
    def close(self):
        if self.cap:
            self.cap.release()
        self.is_connected = False
