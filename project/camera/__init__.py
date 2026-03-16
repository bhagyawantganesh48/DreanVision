"""
capture/__init__.py
====================
Package marker – exposes the high-level convenience imports for the
DreamVision Phase-1 capture module.
"""

from camera.camera_interface import build_camera, ThermalCamera, RGBCamera
from camera.image_processing  import ThermalProcessor, ProcessedFrame, FrameStats
from camera.data_storage      import ensure_directories, save_thermal_image, \
                                      save_rgb_image, append_metadata, next_frame_id, \
                                      read_metadata_all
from camera.logger_setup      import setup_logging

__all__ = [
    "build_camera", "ThermalCamera", "RGBCamera",
    "ThermalProcessor", "ProcessedFrame", "FrameStats",
    "ensure_directories", "save_thermal_image", "save_rgb_image",
    "append_metadata", "next_frame_id", "read_metadata_all",
    "setup_logging",
]
