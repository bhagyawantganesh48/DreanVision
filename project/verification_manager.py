"""
database/verification_manager.py
================================
Provides queries to handle supervisor verification workflows
and traceability searches.
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict
from database.db import get_connection

logger = logging.getLogger("dreamvision.verification")

def verify_inspection(part_uid: str, verified_status: str, verified_by: str) -> bool:
    """
    Updates an inspection with a supervisor's verification decision.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat(timespec="seconds")
        
        cursor.execute("""
            UPDATE parts_inspection
            SET verified_status = ?, verified_by = ?, verification_timestamp = ?, sync_status = 'PENDING'
            WHERE part_uid = ?
        """, (verified_status, verified_by, now, part_uid))
        
        if cursor.rowcount > 0:
            conn.commit()
            logger.info(f"VERIFIED: {verified_by} verified {part_uid} as {verified_status}")
            return True
        return False

def search_inspections(search_term: str = "", limit: int = 50) -> List[Dict]:
    """
    Search inspections by ID or Component Name.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        if search_term:
            query = f"%{search_term}%"
            cursor.execute("""
                SELECT * FROM parts_inspection 
                WHERE part_uid LIKE ? OR component_name LIKE ?
                ORDER BY timestamp DESC LIMIT ?
            """, (query, query, limit))
        else:
            cursor.execute("SELECT * FROM parts_inspection ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

def filter_inspections(status: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """
    Returns filtered inspections based on status.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT * FROM parts_inspection WHERE status = ? ORDER BY timestamp DESC LIMIT ?", (status, limit))
        else:
            cursor.execute("SELECT * FROM parts_inspection ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]
