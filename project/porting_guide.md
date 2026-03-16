# DreamVision Porting Guide
**How to move the project to a new laptop**

To successfully port the latest DreamVision project (ESP32 → Node.js → Web Dashboard) to a new laptop, you do **not** need to copy the entire repository. Many folders contain old legacy code, tests, or machine learning experiments.

Copy exactly these files and folders to your new laptop to get the active stream working immediately:

## 1. Core Root Files
*   `run_live_thermal.py` *(The main script that connects to your ESP32)*
*   `requirements.txt` *(Required to install Python libraries via `pip install -r requirements.txt`)*
*   `.env` *(Crucial: contains your `MONGO_URI` cloud database string)*

## 2. Python Modules (Required by `run_live_thermal.py`)
Copy these top-level Python packages completely:
*   **`camera/` folder:**
    *   `camera/config.py`
    *   `camera/camera_interface.py`
    *   `camera/esp32_stream.py`
    *   `camera/simulator.py`
    *   `camera/image_processing.py`
*   **`database/` folder:**
    *   `database/db.py`
    *   `database/mongo_db.py`
*   **`data/` folder:**
    *   `data/components.csv` *(Required to load your temperature thresholds into the database)*

## 3. The New Node.js Express Backend
*   **`express_backend/` folder:**
    *   `express_backend/index.js` *(The Express API Server)*
    *   `express_backend/package.json` *(Required to run `npm install` on the new laptop)*

## 4. The HTML Web Dashboard
*   **`frontend/` folder:**
    *   `frontend/index.html`
    *   `frontend/style.css`
    *   `frontend/script.js`
    *   *(Note: The `digital_twin.js` file is no longer used and can be skipped).*

## 5. ESP32 Firmware (Optional)
If you need to flash a brand new ESP32 board from the new laptop, you will also need:
*   **`firmware/` folder:**
    *   `firmware/platformio.ini`
    *   `firmware/src/main.cpp`

---

## 🚀 Setup Steps on the New Laptop:
1. Install **Python 3.10+** and **Node.js**.
2. Run `pip install -r requirements.txt` in the root folder.
3. Open a terminal in `express_backend` and run `npm install`, then `node index.js`.
4. Open a second terminal in `frontend` and run `python -m http.server 8080`.
5. Open a third terminal in the root folder and run `python run_live_thermal.py --esp32` (make sure you connect to the ESP32's WiFi first).
6. Open your browser to `http://localhost:8080`.
