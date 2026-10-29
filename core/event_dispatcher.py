"""
Robust Distributed Event Dispatcher for Camera Nodes.
Handles local durable queuing (SQLite), authentication handshake,
retry mechanisms, and ACK confirmations for zero event loss.
"""
import time
import json
import uuid
import sqlite3
import datetime
import threading
import logging
from pathlib import Path
from typing import Dict, Optional, Callable
import asyncio
import websockets

import config

logger = logging.getLogger("CameraDispatcher")

class CameraEventDispatcher:
    """
    Resilient edge event dispatcher.
    - Queues events into a local SQLite store so events are preserved across power cuts / network outages.
    - Sends authentication handshake to central server.
    - Retries until server acknowledges (ACK).
    - Ensures camera capture thread is NEVER blocked.
    """
    def __init__(self, endpoint_url: str, camera_id: str = "CAM-01",
                 camera_location: str = "Main Entrance Gate",
                 auth_token: str = config.CAMERA_AUTH_TOKEN,
                 cooldown_seconds: float = 5.0,
                 local_queue_db: Optional[Path] = None,
                 on_event_sent: Optional[Callable] = None):
        self.endpoint_url = endpoint_url
        self.camera_id = camera_id
        self.camera_location = camera_location
        self.auth_token = auth_token
        self.cooldown_seconds = cooldown_seconds
        self.on_event_sent = on_event_sent

        # Local durable queue database
        if local_queue_db is None:
            self.local_queue_db = config.BASE_DIR / f".queue_{self.camera_id}.db"
        else:
            self.local_queue_db = Path(local_queue_db)
            
        self._init_local_db()

        # Cooldown tracker {student_id: float_timestamp}
        self.last_triggered: Dict[str, float] = {}
        self.running = False
        self.worker_thread = None
        self.total_dispatched = 0
        self.last_dispatched_student = None
        self.last_dispatched_status = None
        self.last_dispatched_time = 0.0

    def _init_local_db(self):
        with sqlite3.connect(self.local_queue_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_events (
                    event_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    retry_count INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def start(self):
        """Starts background transmission worker."""
        if self.running:
            return self
        self.running = True
        self.worker_thread = threading.Thread(target=self._run_async_worker, daemon=True, name=f"Dispatcher-{self.camera_id}")
        self.worker_thread.start()
        logger.info(f"Dispatcher started for [{self.camera_id}] targeting: {self.endpoint_url}")
        return self

    def trigger_sighting(self, student_id: str, status: str, confidence: float,
                         bbox: tuple, latency_ms: float,
                         margin: float = 1.0,
                         student_info: Optional[dict] = None) -> bool:
        """
        Non-blocking trigger. Checks cooldown, generates idempotent UUID,
        persists into local queue, and notifies background worker.
        """
        now = time.time()
        last_time = self.last_triggered.get(student_id, 0.0)

        # Apply cooldown per identity
        if now - last_time < self.cooldown_seconds:
            return False

        self.last_triggered[student_id] = now
        self.last_dispatched_student = student_id
        self.last_dispatched_status = status
        self.last_dispatched_time = now

        x, y, w, h = bbox
        local_iso = datetime.datetime.now().astimezone().isoformat()
        name = student_info.get("name", student_id) if student_info else student_id
        event_id = str(uuid.uuid4())

        payload = {
            "type": "EVENT",
            "event_id": event_id,
            "event": "STUDENT_SIGHTING",
            "student_id": student_id,
            "student_name": name,
            "status": status, # "VERIFIED" or "UNCERTAIN"
            "camera_id": self.camera_id,
            "camera_location": self.camera_location,
            "observed_at": local_iso,
            "confidence": round(float(confidence), 4),
            "margin": round(float(margin), 4),
            "bbox": {"x": int(x), "y": int(y), "width": int(w), "height": int(h)},
            "inference_latency_ms": round(float(latency_ms), 2),
            "metadata": student_info or {}
        }

        # Store into local SQLite queue
        try:
            with sqlite3.connect(self.local_queue_db) as conn:
                conn.execute(
                    "INSERT INTO pending_events (event_id, student_id, payload, created_at) VALUES (?, ?, ?, ?)",
                    (event_id, student_id, json.dumps(payload), now)
                )
                conn.commit()
            logger.info(f"Queued [{status}] sighting for Student [{student_id}] (Event ID: {event_id[:8]}...)")
            if self.on_event_sent:
                self.on_event_sent(payload)
            return True
        except Exception as e:
            logger.error(f"Failed to persist event to local queue: {e}")
            return False

    def _run_async_worker(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connection_loop())
        finally:
            loop.close()

    async def _connection_loop(self):
        """WebSocket sender with auto-detection for NetCam server or CentralServer."""
        is_netcam = "/ws/cameras" in self.endpoint_url or ":8000" in self.endpoint_url

        while self.running:
            try:
                connect_url = self.endpoint_url
                if is_netcam and "?" not in connect_url:
                    connect_url = f"{self.endpoint_url}?camera_id={self.camera_id}"
                    if self.auth_token:
                        connect_url += f"&token={self.auth_token}"

                logger.info(f"Connecting to Server ({'NetCam' if is_netcam else 'CentralServer'}): {connect_url}...")
                async with websockets.connect(connect_url, open_timeout=5, close_timeout=3) as ws:
                    if is_netcam:
                        # NetCam camera.connected handshake
                        handshake = {
                            "schema_version": "1.0",
                            "event_id": str(uuid.uuid4()),
                            "event_type": "camera.connected",
                            "camera": {
                                "camera_id": self.camera_id,
                                "location_id": self.camera_location,
                                "device_type": "cctv_camera",
                                "display_name": f"YuNet SFace Node ({self.camera_id})"
                            },
                            "session_id": "session-2026",
                            "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                        }
                        await ws.send(json.dumps(handshake))
                        ack_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        logger.info(f"Connected to NetCam! Server ACK: {ack_raw[:80]}...")
                    else:
                        # CentralServer AUTH handshake
                        auth_msg = {
                            "type": "AUTH",
                            "token": self.auth_token,
                            "camera_id": self.camera_id,
                            "camera_location": self.camera_location
                        }
                        await ws.send(json.dumps(auth_msg))
                        auth_resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        auth_data = json.loads(auth_resp)
                        if auth_data.get("status") != "AUTH_OK":
                            logger.error(f"Authentication failed: {auth_data.get('message')}. Waiting 10s...")
                            await asyncio.sleep(10)
                            continue
                        logger.info("Authenticated with Central Server successfully.")

                    last_heartbeat = time.time()

                    # Drain local queue with ACK verification
                    while self.running:
                        now_t = time.time()
                        # NetCam periodic heartbeat every 20s
                        if is_netcam and (now_t - last_heartbeat > 20):
                            hb = {
                                "schema_version": "1.0",
                                "event_id": str(uuid.uuid4()),
                                "event_type": "camera.heartbeat",
                                "camera": {
                                    "camera_id": self.camera_id,
                                    "location_id": self.camera_location,
                                    "device_type": "cctv_camera"
                                },
                                "session_id": "session-2026",
                                "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                            }
                            await ws.send(json.dumps(hb))
                            await ws.recv()
                            last_heartbeat = now_t

                        pending = self._fetch_pending_events(batch_size=10)
                        if not pending:
                            await asyncio.sleep(0.5)
                            continue

                        for event_id, payload_str in pending:
                            payload_obj = json.loads(payload_str)
                            
                            # If sending to NetCam, adapt to NetCam protocol v1.0
                            if is_netcam:
                                b_dict = payload_obj.get("bbox", {})
                                netcam_payload = {
                                    "schema_version": "1.0",
                                    "event_id": event_id,
                                    "event_type": "student.sighting",
                                    "camera": {
                                        "camera_id": self.camera_id,
                                        "location_id": self.camera_location,
                                        "device_type": "cctv_camera",
                                        "display_name": f"YuNet SFace Node ({self.camera_id})"
                                    },
                                    "session_id": "session-2026",
                                    "observed_at": payload_obj.get("observed_at"),
                                    "student": {
                                        "student_id": payload_obj.get("student_id"),
                                        "identity_status": "recognized" if payload_obj.get("status") == "VERIFIED" else "uncertain"
                                    },
                                    "observation": {
                                        "observed_at": payload_obj.get("observed_at"),
                                        "recognition_confidence": payload_obj.get("confidence"),
                                        "track_id": f"track-{payload_obj.get('student_id')}"
                                    },
                                    "face": {
                                        "bounding_box": {
                                            "x": max(0.0, min(1.0, round(float(b_dict.get("x", 0)) / 640.0, 4))),
                                            "y": max(0.0, min(1.0, round(float(b_dict.get("y", 0)) / 480.0, 4))),
                                            "width": max(0.0, min(1.0, round(float(b_dict.get("width", 0)) / 640.0, 4))),
                                            "height": max(0.0, min(1.0, round(float(b_dict.get("height", 0)) / 480.0, 4)))
                                        },
                                        "quality": "high",
                                        "landmarks_detected": True
                                    }
                                }
                                to_send = json.dumps(netcam_payload)
                            else:
                                to_send = payload_str

                            await ws.send(to_send)
                            
                            # Wait for ACK
                            try:
                                ack_raw = await asyncio.wait_for(ws.recv(), timeout=4.0)
                                ack = json.loads(ack_raw)
                                if ack.get("status") in ("ACK", "ack") or ack.get("event_id") == event_id:
                                    self._delete_event(event_id)
                                    self.total_dispatched += 1
                                    logger.debug(f"Event {event_id[:8]} ACKed.")
                                else:
                                    logger.warning(f"Unexpected response for {event_id}: {ack}")
                            except asyncio.TimeoutError:
                                logger.warning(f"Timeout waiting for ACK on {event_id[:8]}. Will retry.")
                                break
                                
                        await asyncio.sleep(0.1)

            except Exception as e:
                if self.running:
                    logger.warning(f"Connection issue ({e}). Retrying in 4 seconds...")
                    await asyncio.sleep(4)

    def _fetch_pending_events(self, batch_size=10):
        with sqlite3.connect(self.local_queue_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT event_id, payload FROM pending_events ORDER BY created_at ASC LIMIT ?", (batch_size,))
            return cursor.fetchall()

    def _delete_event(self, event_id: str):
        with sqlite3.connect(self.local_queue_db) as conn:
            conn.execute("DELETE FROM pending_events WHERE event_id = ?", (event_id,))
            conn.commit()

    def stop(self):
        """Stops background dispatcher."""
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)
        logger.info(f"Dispatcher for [{self.camera_id}] stopped.")
