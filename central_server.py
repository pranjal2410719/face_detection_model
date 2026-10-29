"""
Central Event Receiver & Trajectory Store Server.
Authenticates camera nodes, validates schemas, deduplicates events,
persists sightings to JSONL & indexed SQLite, and delivers acknowledgements.
"""
import os
import sys
import json
import sqlite3
import asyncio
import datetime
import logging
from contextlib import closing
from pathlib import Path
from typing import Dict, Any, Optional
import websockets

import config

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [CentralServer] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("CentralServer")

class CentralEventStore:
    """
    Durable multi-tier storage engine:
      1. Append-only JSONL log (central_storage/events.jsonl)
      2. Indexed SQLite database (central_storage/events.db)
    """
    def __init__(self, storage_dir: Path):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.storage_dir / "events.jsonl"
        self.db_path = self.storage_dir / "events.db"
        self._init_sqlite()

    def _init_sqlite(self):
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sightings (
                    event_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    student_name TEXT,
                    status TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    camera_location TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    bbox_x INTEGER,
                    bbox_y INTEGER,
                    bbox_w INTEGER,
                    bbox_h INTEGER,
                    latency_ms REAL,
                    raw_payload TEXT NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_student_time ON sightings(student_id, observed_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_camera_time ON sightings(camera_id, observed_at)")
            conn.commit()
        logger.info(f"Durable event store initialized at: {self.storage_dir}")

    def is_duplicate(self, event_id: str) -> bool:
        """Checks if event_id already exists in store."""
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM sightings WHERE event_id = ?", (event_id,))
            return cursor.fetchone() is not None

    def persist(self, event: Dict[str, Any], received_at: str) -> bool:
        """
        Durably persists sighting. Returns True if inserted, False if duplicate.
        """
        event_id = event.get("event_id")
        if not event_id:
            logger.warning("Event missing 'event_id'; rejected.")
            return False

        if self.is_duplicate(event_id):
            logger.info(f"Deduplicated duplicate event_id: {event_id}")
            return False

        bbox = event.get("bbox", {})
        
        # 1. Insert into indexed SQLite
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sightings (
                    event_id, student_id, student_name, status, camera_id, camera_location,
                    confidence, observed_at, received_at, bbox_x, bbox_y, bbox_w, bbox_h,
                    latency_ms, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id,
                event.get("student_id"),
                event.get("student_name"),
                event.get("status", "VERIFIED"),
                event.get("camera_id"),
                event.get("camera_location"),
                float(event.get("confidence", 0.0)),
                event.get("observed_at"),
                received_at,
                int(bbox.get("x", 0)),
                int(bbox.get("y", 0)),
                int(bbox.get("width", 0)),
                int(bbox.get("height", 0)),
                float(event.get("inference_latency_ms", 0.0)),
                json.dumps(event)
            ))
            conn.commit()

        # 2. Append to JSONL audit log
        audit_record = dict(event)
        audit_record["server_received_at"] = received_at
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(audit_record) + "\n")

        return True

    def query_trajectory(self, student_id: str, date_str: Optional[str] = None, 
                         status_filter: Optional[str] = None) -> list:
        """
        Retrieves chronological sightings across all cameras for a student.
        """
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            query = "SELECT * FROM sightings WHERE student_id = ?"
            params = [student_id]

            if date_str:
                query += " AND observed_at LIKE ?"
                params.append(f"{date_str}%")
            if status_filter:
                query += " AND status = ?"
                params.append(status_filter)

            query += " ORDER BY observed_at ASC"
            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [dict(r) for r in rows]

class CentralServer:
    def __init__(self, host: str, port: int, auth_token: str, storage_dir: Path):
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.store = CentralEventStore(storage_dir)

    async def handle_connection(self, websocket):
        client_ip = websocket.remote_address
        logger.info(f"New camera node connecting from: {client_ip}")
        authenticated = False
        camera_meta = {}

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"status": "ERROR", "message": "Invalid JSON"}))
                    continue

                msg_type = data.get("type", "EVENT")

                # Handle Handshake / Authentication
                if not authenticated:
                    if msg_type == "AUTH":
                        provided_token = data.get("token")
                        if provided_token == self.auth_token:
                            authenticated = True
                            camera_meta = {
                                "camera_id": data.get("camera_id", "UNKNOWN"),
                                "location": data.get("camera_location", "UNKNOWN")
                            }
                            logger.info(f"Node [{camera_meta['camera_id']}] authenticated at '{camera_meta['location']}'")
                            await websocket.send(json.dumps({
                                "status": "AUTH_OK",
                                "message": "Authentication successful",
                                "server_time": datetime.datetime.now().astimezone().isoformat()
                            }))
                            continue
                        else:
                            logger.warning(f"Authentication failed for {client_ip}: Invalid token.")
                            await websocket.send(json.dumps({"status": "AUTH_FAILED", "message": "Invalid token"}))
                            await websocket.close(4001, "Unauthorized")
                            return
                    else:
                        await websocket.send(json.dumps({"status": "AUTH_REQUIRED", "message": "Must send AUTH message first"}))
                        await websocket.close(4002, "Authentication required")
                        return

                # Process Authenticated Event
                if msg_type == "EVENT" or "event" in data:
                    received_at = datetime.datetime.now().astimezone().isoformat()
                    event_id = data.get("event_id")
                    student_id = data.get("student_id")
                    status = data.get("status", "VERIFIED")

                    # Persist event
                    is_new = self.store.persist(data, received_at=received_at)
                    
                    if is_new:
                        logger.info(
                            f"PERSISTED [{status}] sighting: Student [{student_id}] "
                            f"by [{data.get('camera_id')}] at '{data.get('camera_location')}' "
                            f"(Observed: {data.get('observed_at')})"
                        )

                    # Acknowledge receipt
                    ack = {
                        "status": "ACK",
                        "event_id": event_id,
                        "student_id": student_id,
                        "server_received_at": received_at,
                        "deduplicated": not is_new
                    }
                    await websocket.send(json.dumps(ack))

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Camera node disconnected: {camera_meta.get('camera_id', client_ip)}")

    async def start(self):
        logger.info(f"Central Server listening on ws://{self.host}:{self.port}...")
        logger.info(f"Storage path: {self.store.storage_dir}")
        async with websockets.serve(self.handle_connection, self.host, self.port):
            await asyncio.Future() # run forever

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Central Event Receiver & Trajectory Store")
    parser.add_argument("--host", default=config.CENTRAL_SERVER_HOST)
    parser.add_argument("--port", type=int, default=config.CENTRAL_SERVER_PORT)
    parser.add_argument("--token", default=config.CAMERA_AUTH_TOKEN)
    parser.add_argument("--storage", default=str(config.CENTRAL_STORAGE_DIR))
    args = parser.parse_args()

    server = CentralServer(args.host, args.port, args.token, Path(args.storage))
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("Central Server stopped.")

if __name__ == "__main__":
    main()
