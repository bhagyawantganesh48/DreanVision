"""
database/db.py
===============
SQLite local edge database setup and queries.
"""

import sqlite3
import os
import logging
from typing import Optional, Dict

logger = logging.getLogger("dreamvision.database")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "edge_inspection.db")

def get_connection() -> sqlite3.Connection:
    """Returns a connected SQLite DB instance with Row factory."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create the component_temperature_rules and parts_inspection tables if they do not exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Dataset Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS component_temperature_rules (
                component_name TEXT PRIMARY KEY,
                normal_temp_min REAL,
                normal_temp_max REAL,
                critical_temp REAL,
                failure_temp REAL
            )
        """)
        
        # 2. Inspection Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS parts_inspection (
                part_uid TEXT PRIMARY KEY,
                component_name TEXT,
                temperature REAL,
                status TEXT,
                device_id TEXT,
                image_path TEXT,
                timestamp TEXT,
                sync_status TEXT DEFAULT 'PENDING',
                verified_status TEXT DEFAULT 'Pending',
                verified_by TEXT,
                verification_timestamp TEXT
            )
        """)
        
        # 3. Anomalies Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inspection_anomalies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                component TEXT,
                anomaly_type TEXT,
                description TEXT
            )
        """)
        
        # 4. Insert Default Rules (Step-7 helper)
        default_rules = [
            ("crankcase", 30.0, 75.0, 95.0, 120.0),
            ("heat_exchanger", 25.0, 65.0, 85.0, 110.0),
            ("main_bearing", 35.0, 80.0, 100.0, 130.0),
            ("valve_manifold", 20.0, 55.0, 75.0, 100.0)
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO component_temperature_rules 
            (component_name, normal_temp_min, normal_temp_max, critical_temp, failure_temp)
            VALUES (?, ?, ?, ?, ?)
        """, default_rules)
        conn.commit()
    logger.info(f"Database initialized at {DB_PATH}")

def fetch_rule(component_name: str) -> Optional[Dict]:
    """Retrieve temperature thresholds for a component."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM component_temperature_rules WHERE component_name = ?", 
            (component_name,)
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None

def insert_inspection(part_uid: str, component_name: str, temperature: float, status: str, device_id: str, image_path: str, timestamp: str, sync_status: str = 'PENDING'):
    """Store the final inspection record."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO parts_inspection 
            (part_uid, component_name, temperature, status, device_id, image_path, timestamp, sync_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (part_uid, component_name, temperature, status, device_id, image_path, timestamp, sync_status))
        conn.commit()

def get_pending_inspections() -> list:
    """Retrieve inspections that have not been synced yet."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM parts_inspection WHERE sync_status = 'PENDING'")
        return [dict(row) for row in cursor.fetchall()]

def mark_inspections_synced(part_uids: list):
    """Mark a list of inspections as synced."""
    if not part_uids: return
    with get_connection() as conn:
        cursor = conn.cursor()
        query = "UPDATE parts_inspection SET sync_status = 'SYNCED' WHERE part_uid IN ({})".format(','.join('?'*len(part_uids)))
        cursor.execute(query, tuple(part_uids))
        conn.commit()

def get_all_inspections() -> list:
    """Retrieve all inspections."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM parts_inspection ORDER BY timestamp DESC")
        return [dict(row) for row in cursor.fetchall()]

def get_inspection_by_id(part_uid: str) -> Optional[Dict]:
    """Retrieve an inspection by its ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM parts_inspection WHERE part_uid = ?", (part_uid,))
        row = cursor.fetchone()
        if row: return dict(row)
    return None

