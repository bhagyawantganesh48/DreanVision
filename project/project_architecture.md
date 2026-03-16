# DreamVision Smart Factory - Project Architecture & Data Flow

## 1. System Overview
DreamVision is an Industry 4.0 Thermal AI Inspection system designed to capture live thermal telemetry from edge devices (e.g., ESP32 connected to MLX90640), process anomalies locally, and synchronize telemetry with a cloud backend (MongoDB Atlas). 

The platform is capable of running a full suite of services locally on a single machine or edge gateway, orchestrated by main launcher scripts like `run_dreamvision.py` and `run_live_thermal.py`.

## 2. Global Architecture Layout

The application relies on a polyglot microservice pattern running in tandem:
- **Thermal Edge Capture (Python)**: Interfaces with the camera hardware, performs realtime stats calculations, applies boundary boxes, and generates OpenCV displays. 
- **Capture API Edge Server (Python - Port 8001)**: Intermediary for handling stream capture data.
- **Edge AI Server / UI Host (Python - Port 8002)**: Primarily hosts the dashboard endpoints, broadcasts new telemetry, and serves the static HTML/JS frontend assets (`/app/index.html`).
- **Cloud Express Backend (Node.js - Port 3000)**: Serves as the cloud-facing REST API providing aggregated statistics, historical queries, and cloud integrations (connecting to MongoDB).
- **Control Center Frontend**: Pure HTML/JS/CSS application utilizing WebSockets/HTTP polling to fetch telemetry from the servers. Includes a Digital Twin (`digital_twin.js`) to render components visually.

### High-Level Directory Structure
```
DreanVision-main/
├── run_dreamvision.py         # 1-Click Platform Launcher (Starts API, Edge Server, Live View)
├── main_api.py                # Capture API Server (Port 8001)
├── edge_server/               # Edge AI Server hosting dashboard + endpoints (Port 8002)
├── frontend/                  # Static HTML/JS Dashboard files (served by edge_server)
├── express_backend/           # Node.js API facing MongoDB (Port 3000)
│   ├── index.js               # Express app with endpoints (/results, /stats, /inspection)
│   └── package.json           # Node dependencies
├── project/                   # Core Edge Computing Logic
│   ├── run_live_thermal.py    # Dedicated launcher for Live Thermal OpenCV window & syncing
│   ├── camera/                # Hardware interfacing (esp32_stream.py, camera_interface.py)
│   ├── data/                  # Edge SQLite storage and snapshots destination
│   ├── database/              # SQLite edge DB logic (db.py, anomaly_manager.py)
│   └── mongo_db.py            # MongoDB sync client
└── database/                  # SQLite models and schemas for root level launchers
```

## 3. Data Flow & Streaming Pipelines

### A. Real-Time Hardware Streaming Pipeline (Firmware -> Edge)
1. **Source (Firmware)**: An ESP32 interfaces with an MLX90640 thermal sensor via I2C. The ESP32 firmware (`main.cpp`) acts as a TCP server on port 5000, streaming raw floating-point arrays (768 floats representing 32x24 pixels) across the local WiFi to the Edge PC. 
2. **Ingestion (Python)**: Internal `camera_interface.py` decodes the incoming 3072-byte buffer streams into generic numpy float arrays.
3. **Filtering & Analysis (`inspection_engine/pipeline.py`)**: The frames are sent to `image_processing.py` where statistics (min, mean, max) and hotspot coordinates are calculated using mathematical operations on the Numpy arrays. 
4. **Evaluation Engine**: The engine fetches the normal/critical thresholds locally from the `component_temperature_rules` SQLite table, evaluates the live maximum temperature, and assigns an `OK/WARNING/NOK` status.
5. **Visualization & Capture**: NumPy arrays are converted to BGR color maps via OpenCV, overlaid with bounding boxes and statuses, and displayed via `cv2.imshow()`. Simultaneously, `save_thermal_image()` commits snapshots to `data/thermal_images/`.

### B. Storage & Cloud Upload Pipeline (Distributed Processing)
To prevent network latency from disrupting the real-time camera FPS, data storage is decoupled:
1. **Edge Persist**: A snapshot is triggered. The statistics, rule metadata, and status are instantly saved to the **Local SQLite Database** (`parts_inspection` table).
2. **Dashboard Broadcast**: A quick HTTP POST broadcast is triggered to `http://localhost:8002/dashboard/broadcast_new` to instantly update the frontend.
3. **Internal Queue Array**: The record is pushed to a non-blocking, max-sized `queue.Queue`.
4. **Cloud Uploader Thread**: A Daemon thread (`_CloudUploader`) continuously drains this queue, sending asynchronous `update_one(upsert=True)` payloads to **MongoDB Atlas**. 

### C. Frontend Telemetry Flow & Edge UI
1. Load dashboard (`index.html`) via Edge Server (Port 8002). The Edge server statically mounts the frontend (`/app`) and digital twin UI (`/digital_twin`).
2. **WebSockets (Live Feed)**: The Edge Server maintains an active FastAPI `WebSocketManager`. When a snapshot occurs, it broadcasts JSON metadata instantly to all connected UI clients.
3. **History Polling**: The UI fetches historical stats from `/stats` and `/results` via either Edge Server or Express Backend.
4. **Digital Twin Reactivity**: The `digital_twin.js` script dynamically adjusts the fill gradients of 3D SVGs mapping to physical parts (`crankcase`, `heat_exchanger`) based on the latest broadcasted temps.

## 4. Database Models

### Local Edge DB (SQLite - `dreamvision.db` / `edge_inspection.db`)
* **`component_temperature_rules`**: `component_name (PK)`, `normal_temp_min`, `normal_temp_max`, `critical_temp`, `failure_temp`.
* **`parts_inspection`**: `part_uid (PK)`, `component_name`, `temperature`, `status`, `sync_status`, `verified_status`.
* **`inspection_anomalies`**: Track anomalous events over time.

### Cloud DB (MongoDB Atlas)
* **`inspections` Collection**: A flattened NoSQL replicate of `parts_inspection`, including nested hardware stats (min, max, etc.). Acts as the global telemetry warehouse.

---

## 5. Architectural Flaws & Improvement Suggestions

While functional, the current architecture mixes Edge and Cloud responsibilities across overlapping boundaries. Below are prioritized recommendations to stream-line and harden the project for Enterprise deployments.

### A. Reducing Complexity & Deployment Friction
**Current Problem:** Running the platform natively requires managing Python versions, Node.js versions, and avoiding port conflicts manually. Cross-platform execution is brittle.
**Solution (Containerization):**
* Dockerize the application into isolated containers: `thermal-edge` (Python), `cloud-api` (Node.js), `dashboard-ui` (Nginx). 
* Create a `docker-compose.yml` to spin up dependencies seamlessly without manual process monitoring (`_monitor_processes` hack).

### B. Modularization & Message Brokering (Decoupling)
**Current Problem:** Direct HTTP calls between Python sub-scripts and Queue threads mean losing data on crash. The architecture is tightly coupled (Python directly imports local SQLite, while Node reads from Mongo).
**Solution (Event-Driven Architecture):**
* Introduce **MQTT (via Mosquitto)** or **Redis Pub/Sub** on the edge device. 
* The Camera module purely pushes `{temp: 45, hotspot_x: 10}` to `sensor/thermal/raw`.
* An independent `AnomalyDetector` module subscribes to this topic, compares it with SQLite, and publishes to `sensor/thermal/anomaly`.
* The `CloudUploader` subscribes to it and guarantees delivery using an Outbox Pattern instead of a volatile internal Python `queue.Queue`.

### C. Bug & Failure Prevention
**Current Problem:** The SQLite database is accessed by multiple threads/processes concurrently, which leads to `database is locked` errors during high throughput. The Cloud Upload thread silently drops data if the Queue fills up.
**Solution:**
* Implement Async DB operations using something like SQLite WAL mode combined with SQLAlchemy, or upgrade the edge DB to PostgreSQL if running on a competent Edge Gateway.
* Implement a robust Retry/Backoff mechanism for the MongoDB connection instead of silently failing or dropping records when offline.
* Add comprehensive input validation and try-catch blocks in the Express backend (currently basic). Use Zod or Joi to strictly validate incoming payloads.

### D. Strict Backend & Frontend Segregation
**Current Problem:** The Python `edge_server` directly serves the `frontend/` static files (`index.html`, CSS, JS) using FastAPI's `StaticFiles`. This couples the high-compute edge AI code with the UI rendering.
**Solution:**
* Offload the `frontend/` and `dashboard/` directories completely to a dedicated CDN or static host (like Vercel, Netlify, or NGINX).
* The UI should connect exclusively to the Node.js Express Backend for historical data, falling back to specific Edge Server IP addresses ONLY for real-time WebSockets (`ws://<edge-ip>:8002/ws/inspections`).

### E. Firmware Reliability & Upgradeability
**Current Problem:** The ESP32 TCP server acts passively, listening on port 5000. If the Python edge server disconnects, the connection buffers can hang. There is no Over-The-Air (OTA) firmware upgrade route.
**Solution:**
* Modify the ESP32 code to act as an MQTT client publishing directly to a topic (e.g., `dreamvision/thermal/stream`) rather than keeping a raw TCP socket open. 
* Add ArduinoOTA capabilities to the firmware so camera modules deployed in inaccessible factory mounts can be upgraded remotely.
