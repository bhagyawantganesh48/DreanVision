import cv2
import requests
import numpy as np
import time
import base64
import logging
import camera.config as cfg

logger = logging.getLogger("dreamvision.esp32_stream")

class ESP32Stream:
    def __init__(self, host=cfg.ESP32_HOST, port=cfg.ESP32_PORT):
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}/stream"
        self.cap = None
        self.is_connected = False

    def connect(self):
        """Connect with auto-retry logic"""
        while not self.is_connected:
            try:
                logger.info(f"Attempting to connect to ESP32 stream at {self.url}...")
                self.cap = cv2.VideoCapture(self.url)
                if self.cap.isOpened():
                    self.is_connected = True
                    logger.info("ESP32 Stream Connected.")
                    return True
                else:
                    logger.warning("Failed to open stream. Retrying in 5s...")
                    time.sleep(5)
            except Exception as e:
                logger.error(f"Connection error: {e}. Retrying in 5s...")
                time.sleep(5)

    def get_frame(self):
        """Capture and return frame in OpenCV format"""
        if not self.is_connected:
            self.connect()
            
        ret, frame = self.cap.read()
        if not ret:
            logger.warning("Frame capture failed. Attempting reconnect...")
            self.is_connected = False
            return None
        return frame

    def get_thermal_data(self):
        """Convert frame to simulated thermal intensity matrix if needed"""
        frame = self.get_frame()
        if frame is None:
            return None
        # Convert to grayscale to simulate thermal intensity for processing
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return gray.astype(np.float32)

    def close(self):
        if self.cap:
            self.cap.release()
        self.is_connected = False
