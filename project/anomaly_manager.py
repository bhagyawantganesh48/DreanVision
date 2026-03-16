"""
database/anomaly_manager.py
===========================
Queries to insert and retrieve anomalies for the Industry 4.0 Platform.
"""
from typing import List, Dict
from database.db import get_connection

def insert_anomaly(timestamp: str, component: str, anomaly_type: str, description: str):
    """Stores a detected anomaly into the inspection_anomalies table."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO inspection_anomalies (timestamp, component, anomaly_type, description)
            VALUES (?, ?, ?, ?)
        """, (timestamp, component, anomaly_type, description))
        conn.commit()

def get_latest_anomalies(limit: int = 50) -> List[Dict]:
    """Retrieves the latest anomalies."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM inspection_anomalies ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]
