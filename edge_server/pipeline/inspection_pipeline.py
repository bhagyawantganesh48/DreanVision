"""
edge_server/pipeline/inspection_pipeline.py
============================================
Edge pipeline that processes data sourced from the Smart Glasses API.
"""

import os
import base64
import cv2
import numpy as np
import logging
from datetime import datetime

from utils.id_generator import generate_part_uid
from edge_server.vision.component_detector import identify_component
from edge_server.thermal.temperature_extractor import extract_temperature
from inspection_engine.evaluator import evaluate_temperature
from database.db import fetch_rule, insert_inspection

from analytics.anomaly_detection import detect_anomaly
from analytics.predictive_maintenance import run_predictive_maintenance
from analytics.defect_predictor import predict_defect_probability

import threading

# Removed local _get_next_uid to use consolidated generator in utils.id_generator

logger = logging.getLogger("dreamvision.pipeline")

PROCESSED_IMG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "processed_images")

def run_edge_inspection(device_id: str, thermal_b64: str | None, rgb_b64: str | None,
                         timestamp_str: str, component_name_override: str | None = None,
                         simulated_temperature: float | None = None) -> dict:
    """
    1. Decode images (skipped when simulating)
    2. Identify component type from RGB (or use override)
    3. Extract temperature reading from Thermal image (or use simulated_temperature)
    4. Validate OK/NOK state based on SQLite Dataset Rules
    5. Save processed output and commit inspection to DB
    6. Run ML Defect Prediction, Anomaly Detection, and Predictive Maintenance
    7. Return response to Smart Glass endpoint
    """
    logger.info(f"Incoming Edge AI Inspection Request from Device '{device_id}' at {timestamp_str}")

    # 1. Decode payloads (only when a real image is provided)
    thermal_raw = None
    rgb_raw = None
    thermal_float = None
    if thermal_b64:
        thermal_raw = _decode_b64_to_cv2(thermal_b64)
        rgb_raw = _decode_b64_to_cv2(rgb_b64) if rgb_b64 else None
        # Handle BGR images from simplified capture APIs by converting to grayscale
        if thermal_raw is not None and len(thermal_raw.shape) == 3:
            logger.warning("Thermal image received in BGR format; converting to grayscale for pipeline processing.")
            thermal_raw = cv2.cvtColor(thermal_raw, cv2.COLOR_BGR2GRAY)
        thermal_float = thermal_raw.astype(np.float32)

    # 2. Vision Identifier (with optional client-side override)
    if component_name_override:
        component_name = component_name_override
        logger.info(f"Using client-provided component name: '{component_name}'")
    else:
        component_name = identify_component(rgb_raw if rgb_raw is not None else thermal_float)

    # 3. Fetch rules
    rule = fetch_rule(component_name)
    if not rule:
        raise ValueError(f"Component '{component_name}' not registered in dataset DB.")

    # 4. Thermal Extract & Evaluate (with optional simulated temperature override)
    if simulated_temperature is not None:
        peak_temp = simulated_temperature
        # Generate a representative heatmap for the stored image record
        sim_frame = np.full((64, 64), min(peak_temp, 255.0), dtype=np.float32)
        _, heatmap = extract_temperature(sim_frame)
        logger.info(f"Using simulated temperature: {peak_temp}°C for component '{component_name}'")
    else:
        peak_temp, heatmap = extract_temperature(thermal_float)
    status_raw = evaluate_temperature(peak_temp, rule["normal_temp_max"], rule["failure_temp"])
    
    # Map status to user requested labels: OK / WARNING / NOK
    # Logic: Status from evaluator is "OK", "WARNING", "CRITICAL"
    # User wants: OK, WARNING, NOK
    if status_raw == "CRITICAL":
        status = "NOK"
    else:
        status = status_raw

    # ML Prediction
    predicted_prob = predict_defect_probability(component_name, peak_temp)
    logger.info(f"Rule Evaluator: Component '{component_name}' @ {round(peak_temp, 2)}°C -> {status} (ML Defect Prob: {predicted_prob:.2f})")

    # 5. Unique ID - Format: DV-{date}-{time}-{sequence}
    part_uid = generate_part_uid()
    
    # 6. Save image
    img_filename = f"{part_uid}_{datetime.now().strftime('%Y%m%dT%H%M%S')}.png"
    img_path = os.path.join(PROCESSED_IMG_DIR, img_filename)
    
    if not os.path.isdir(PROCESSED_IMG_DIR):
        os.makedirs(PROCESSED_IMG_DIR, exist_ok=True)
    
    cv2.imwrite(img_path, heatmap)
    rel_path = f"data/processed_images/{img_filename}"
    
    # 7. Local File Storage - Including device_id as requested in Step 3
    insert_inspection(part_uid, component_name, peak_temp, status, device_id, rel_path, timestamp_str)

    # 8. Trigger Background Factory Analytics
    detect_anomaly(component_name, peak_temp, timestamp_str)
    maint = run_predictive_maintenance(component_name, peak_temp)

    # Output dictionary matches prompt constraints perfectly
    response = {
        "part_uid": part_uid,
        "component_name": component_name,
        "temperature": float(np.round(peak_temp, 2)),
        "status": status,
        "device_id": device_id,
        "timestamp": timestamp_str,
        "image_path": rel_path,
        "predicted_defect_probability": predicted_prob
    }
    
    if maint:
        response["maintenance_alert"] = maint
    
    return response

def _decode_b64_to_cv2(b64_str: str) -> np.ndarray:
    """Helper to convert base64 image strings into OpenCV MAT arrays"""
    img_data = base64.b64decode(b64_str)
    np_arr = np.frombuffer(img_data, np.uint8)
    return cv2.imdecode(np_arr, cv2.IMREAD_UNCHANGED)
