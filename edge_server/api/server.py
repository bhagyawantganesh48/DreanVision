"""
edge_server/api/server.py
==========================
FastAPI REST endpoint receiving data directly from the Smart Glass devices
over the local factory WiFi subnet.
"""

import os
import sys
import logging
import asyncio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List

# Setup imports correctly for absolute resolution when run inside `dreamvision/` parent
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from camera import setup_logging
from database.db import get_all_inspections, get_inspection_by_id, init_db
from edge_server.pipeline.inspection_pipeline import run_edge_inspection
from edge_server.cloud.cloud_sync import CloudSynchronizer
from dashboard.api.dashboard_routes import dashboard_router
from fastapi.staticfiles import StaticFiles
from analytics.defect_predictor import load_model, train_model

# Init base configurations
setup_logging()
init_db()
logger = logging.getLogger("dreamvision.api")

app = FastAPI(title="DreamVision Edge Server API", version="5.0.0")

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass # Stale connection

ws_manager = ConnectionManager()

# Mount APIs to existing structures dynamically connecting Phase 4 Dashboard endpoints
app.include_router(dashboard_router)

# Mount Image & Web folders correctly so dashboards display graphics natively
STATIC_IMAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
if os.path.exists(STATIC_IMAGE_DIR):
    app.mount("/data", StaticFiles(directory=STATIC_IMAGE_DIR), name="images")

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    
DIGITAL_TWIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "digital_twin")
if os.path.exists(DIGITAL_TWIN_DIR):
    pass # app.mount is handled after app definition

# Instantiate the cloud worker
cloud_worker = CloudSynchronizer(interval_seconds=15)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start background sync worker
    cloud_worker.start()
    load_model()
    logger.info("Edge Server active on Factory Network.")
    yield
    # Shutdown: Stop background sync worker
    cloud_worker.stop()
    logger.info("Edge Server shutting down.")

app = FastAPI(title="DreamVision Edge Server API", version="5.0.0", lifespan=lifespan)

# Mount APIs to existing structures dynamically connecting Phase 4 Dashboard endpoints
app.include_router(dashboard_router)

# Mount Image & Web folders correctly so dashboards display graphics natively
if os.path.exists(STATIC_IMAGE_DIR):
    app.mount("/data", StaticFiles(directory=STATIC_IMAGE_DIR), name="images")

if os.path.exists(FRONTEND_DIR):
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    
if os.path.exists(DIGITAL_TWIN_DIR):
    app.mount("/digital_twin", StaticFiles(directory=DIGITAL_TWIN_DIR), name="dtwin")

class InspectRequest(BaseModel):
    device_id: str
    thermal_image: Optional[str] = None   # Optional when using simulated_temperature
    rgb_image: Optional[str] = None
    timestamp: str
    component_name: Optional[str] = None          # Override vision detection
    simulated_temperature: Optional[float] = None  # Skip image decode; use value directly

@app.post("/inspect", tags=["Device API"])
async def inspect_component(req: InspectRequest):
    """
    Called by the Smart Glasses device over localized WiFi.
    Decodes the thermal images, executes the pipeline, and generates
    the part inspection summary directly tracking against CSV dataset matrices.
    """
    try:
        response = await asyncio.to_thread(
            run_edge_inspection,
            req.device_id,
            req.thermal_image,
            req.rgb_image,
            req.timestamp,
            req.component_name,
            req.simulated_temperature
        )
        
        # Broadcast to Digital Twin Dashboard
        await ws_manager.broadcast(response)
        
        return JSONResponse(content=response)
    
    except Exception as e:
        logger.error(f"Failed to execute inspection pipeline: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/inspections", tags=["Supervisor Dashboard"])
async def list_inspections():
    """
    Dashboard API enumerating all history stored on the edge appliance 
    before or after it synchronizes to the cloud.
    """
    return get_all_inspections()

@app.get("/inspection/{part_uid}", tags=["Supervisor Dashboard"])
async def fetch_inspection(part_uid: str):
    """Fetch specific breakdown metrics for an audited part UID."""
    result = get_inspection_by_id(part_uid)
    if not result:
        raise HTTPException(status_code=404, detail=f"Part UID {part_uid} not found in Edge DB")
    return result

@app.websocket("/ws/inspections")
async def websocket_inspections(websocket: WebSocket):
    """Real-time live broadcast channel for new inspection events to the digital twin & dashboard."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # maintain connection
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

@app.post("/dashboard/broadcast_new", tags=["Supervisor Dashboard"])
async def broadcast_new_inspection(req: dict):
    """
    Internal endpoint called by external scripts (like run_live_thermal.py) 
    to instantly update the Web Dashboard UI when a manual snapshot is taken.
    """
    await ws_manager.broadcast(req)
    return {"status": "broadcasted"}

@app.post("/train_model", tags=["Factory AI"])
async def trigger_ml_training():
    """Triggers the Scikit-Learn Data Science pipeline using collected history."""
    import asyncio
    success = await asyncio.to_thread(train_model)
    if not success:
        raise HTTPException(status_code=400, detail="Not enough historical data to generate a model.")
    return {"message": "Defect Predictor retrained successfully", "status": "Ready"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("edge_server.api.server:app", host="0.0.0.0", port=8002, reload=False)
