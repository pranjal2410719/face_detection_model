"""
Camera Node Web Server & Centralized Dashboard
==============================================
Provides two distinct environments:
  1. Client-Side Camera Node (/) - Minimal, distraction-free edge camera application.
     Contains: Camera screen, node settings, connected peer nodes, deduplicated student log,
     and data sent logs.
  2. Centralized Dashboard (/dashboard) - Master command view collecting all data:
     Multi-camera matrix with live feeds, cross-camera trajectory tracking,
     complete SQLite audit logs, and enrolled biometrics.
"""
import os
import sys
import time
import json
import base64
import uuid
import sqlite3
import datetime
import logging
import asyncio
from contextlib import closing
from pathlib import Path
from typing import Dict, List, Optional, Any, Set

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from core.detector import FaceDetector
from core.recognizer import FaceRecognizer
from core.student_db import StudentDatabase
from core.event_dispatcher import CameraEventDispatcher
from core.visualizer import Visualizer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [WebNode] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("WebNode")

app = FastAPI(title="Edge Camera Node & Centralized Dashboard", version="2.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Face Engine & Database instances
detector: Optional[FaceDetector] = None
recognizer: Optional[FaceRecognizer] = None
student_db: Optional[StudentDatabase] = None
visualizer: Optional[Visualizer] = None

# Active Dispatchers per camera_id
dispatchers: Dict[str, CameraEventDispatcher] = {}

# Active Camera Nodes Registry {camera_id: dict}
active_nodes: Dict[str, Dict[str, Any]] = {}

# Cross-Camera Trajectory Tracking {student_id: dict}
student_trajectories: Dict[str, Dict[str, Any]] = {}

# Active WebSockets
camera_sockets: Dict[str, WebSocket] = {}
dashboard_sockets: Set[WebSocket] = set()

# In-memory recent sightings buffer
recent_sightings: List[Dict[str, Any]] = []

main_loop: Optional[asyncio.AbstractEventLoop] = None


def handle_dispatcher_ack(event_id: str, student_id: str, ack_payload: dict):
    """Callback invoked from dispatcher background thread upon confirmed ACK from Central Server."""
    logger.info(f"Central Server ACK confirmed for event {event_id[:8]} (Student {student_id})")
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(
            broadcast_ack(event_id, student_id, ack_payload),
            main_loop
        )


async def broadcast_ack(event_id: str, student_id: str, ack_payload: dict):
    payload = {
        "event_id": event_id,
        "student_id": student_id,
        "server_received_at": ack_payload.get("server_received_at"),
        "status": "ACK_RECEIVED"
    }
    await broadcast_to_dashboards("EVENT_ACK", payload)
    for ws in list(camera_sockets.values()):
        try:
            await ws.send_json({"type": "EVENT_ACK", **payload})
        except Exception:
            pass


def load_trajectories_from_db():
    """Reconstructs cross-camera student trajectories from persistent Central Storage."""
    db_path = config.CENTRAL_STORAGE_DIR / "events.db"
    if not db_path.exists():
        return
    try:
        with closing(sqlite3.connect(db_path, timeout=5.0)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT student_id, student_name, camera_id, camera_location, observed_at, confidence
                FROM sightings
                WHERE status = 'VERIFIED' AND student_id IS NOT NULL AND student_id != '' AND student_id != 'UNKNOWN'
                ORDER BY observed_at ASC
            """)
            rows = cursor.fetchall()
            for r in rows:
                stu_id = r["student_id"]
                cam_id = r["camera_id"]
                loc = r["camera_location"]
                obs = r["observed_at"]
                conf = round(float(r["confidence"]) * 100, 1)

                t_str = obs.split("T")[1].split(".")[0] if "T" in obs else obs
                if stu_id not in student_trajectories:
                    student_trajectories[stu_id] = {
                        "student_id": stu_id,
                        "student_name": r["student_name"] or stu_id,
                        "current_camera": cam_id,
                        "current_location": loc,
                        "first_seen": t_str,
                        "last_seen": t_str,
                        "last_seen_epoch": time.time(),
                        "confidence": conf,
                        "trajectory": [{
                            "from_camera": "START",
                            "to_camera": cam_id,
                            "location": loc,
                            "transit_sec": 0.0,
                            "time": t_str
                        }]
                    }
                else:
                    traj = student_trajectories[stu_id]
                    if traj["current_camera"] != cam_id:
                        traj["trajectory"].append({
                            "from_camera": traj["current_camera"],
                            "to_camera": cam_id,
                            "location": loc,
                            "transit_sec": 0.0,
                            "time": t_str
                        })
                        traj["current_camera"] = cam_id
                        traj["current_location"] = loc
                    traj["last_seen"] = t_str
                    traj["confidence"] = conf
        logger.info(f"Loaded {len(student_trajectories)} historical student trajectories from Central Storage.")
    except Exception as e:
        logger.warning(f"Could not load initial trajectories from DB: {e}")


def get_or_create_dispatcher(camera_id: str, location: str, endpoint: str = config.EVENT_ENDPOINT_URL, token: str = config.CAMERA_AUTH_TOKEN) -> CameraEventDispatcher:
    if camera_id not in dispatchers:
        disp = CameraEventDispatcher(
            endpoint_url=endpoint,
            camera_id=camera_id,
            camera_location=location,
            auth_token=token,
            cooldown_seconds=config.EVENT_COOLDOWN_SECONDS,
            on_ack_received=handle_dispatcher_ack
        )
        disp.start()
        dispatchers[camera_id] = disp
    else:
        disp = dispatchers[camera_id]
        disp.update_identity(camera_id, location)
        disp.endpoint_url = endpoint
    return disp


@app.on_event("startup")
async def startup_event():
    global detector, recognizer, student_db, visualizer, main_loop
    logger.info("Initializing Face Engine & Student Database for Camera Network...")
    main_loop = asyncio.get_running_loop()

    detector = FaceDetector(
        model_path=config.DETECTOR_MODEL_PATH,
        conf_threshold=config.DETECTION_CONF_THRESHOLD,
        nms_threshold=config.DETECTION_NMS_THRESHOLD,
        target_size=(320, 240)
    )
    recognizer = FaceRecognizer(model_path=config.RECOGNIZER_MODEL_PATH)
    student_db = StudentDatabase(
        db_dir=config.DATABASE_DIR,
        detector=detector,
        recognizer=recognizer,
        cache_path=config.CACHE_FILE,
        confirmation_threshold=config.CONFIRMATION_THRESHOLD,
        ambiguity_threshold=config.AMBIGUITY_THRESHOLD,
        margin_threshold=config.MARGIN_THRESHOLD
    )
    visualizer = Visualizer(camera_id="EDGE-NODE")
    logger.info(f"Engine ready. Enrolled database has {student_db.get_enrolled_count()} students.")
    load_trajectories_from_db()


@app.on_event("shutdown")
async def shutdown_event():
    for disp in dispatchers.values():
        disp.stop()
    dispatchers.clear()


async def broadcast_to_dashboards(event_type: str, data: Any):
    """Pushes live events to all connected centralized dashboard screens."""
    dead = set()
    payload = json.dumps({"type": event_type, "data": data, "timestamp": time.time()})
    for ws in list(dashboard_sockets):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    for ws in dead:
        dashboard_sockets.discard(ws)


# -----------------------------------------------------------------------------
# REST Endpoints
# -----------------------------------------------------------------------------
@app.api_route("/dashboard", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the Centralized Dashboard."""
    dash_path = Path(__file__).resolve().parent / "static" / "dashboard.html"
    if dash_path.exists():
        return FileResponse(str(dash_path))
    return HTMLResponse("<h1>Dashboard file not found</h1>", status_code=404)


@app.get("/api/node-info")
async def get_node_info():
    """Returns camera node configuration and enrolled student database."""
    enrolled = []
    if student_db:
        for stu_id in student_db.enrolled_ids:
            meta = student_db.metadata.get(stu_id, {})
            enrolled.append({
                "student_id": stu_id,
                "name": meta.get("name", stu_id),
                "department": meta.get("department", "Engineering"),
                "batch": meta.get("batch", "2026")
            })

    central_online = any(d.is_connected for d in dispatchers.values()) if dispatchers else False
    return {
        "node_type": "Edge Camera Node",
        "default_camera_id": config.CAMERA_ID,
        "default_location": config.CAMERA_LOCATION,
        "central_endpoint": config.EVENT_ENDPOINT_URL,
        "central_receiver_online": central_online,
        "detector_model": "OpenCV YuNet (2023mar)",
        "recognizer_model": "OpenCV SFace (2021dec)",
        "enrolled_count": len(enrolled),
        "enrolled_students": enrolled
    }


@app.get("/api/connected-nodes")
async def get_connected_nodes():
    """Returns all camera nodes currently active in the network."""
    now = time.time()
    nodes_list = []
    for cam_id, info in list(active_nodes.items()):
        is_online = (now - info.get("last_heartbeat", 0)) < 12.0
        info["online"] = is_online
        nodes_list.append(info)
    return nodes_list


@app.get("/api/sightings")
async def get_recent_sightings(limit: int = 25):
    """Returns recent sightings in reverse chronological order."""
    return recent_sightings[-limit:][::-1]


@app.get("/api/trajectories")
async def get_trajectories():
    """Returns active cross-camera student trajectories."""
    return list(student_trajectories.values())


def get_db_stats():
    """Returns real SQLite stats from events.db with WAL mode support."""
    db_path = config.CENTRAL_STORAGE_DIR / "events.db"
    if not db_path.exists():
        return {"total_sightings": 0, "unique_verified": 0}
    try:
        with closing(sqlite3.connect(db_path, timeout=5.0)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            c = conn.cursor()
            c.execute("SELECT count(*) FROM sightings")
            total = c.fetchone()[0]
            c.execute("SELECT count(DISTINCT student_id) FROM sightings WHERE status = 'VERIFIED'")
            unique_v = c.fetchone()[0]
            return {"total_sightings": total, "unique_verified": unique_v}
    except Exception as e:
        logger.warning(f"Error reading stats from events.db: {e}")
        return {"total_sightings": 0, "unique_verified": 0}


@app.get("/api/dashboard-stats")
async def get_dashboard_stats():
    """Returns system-wide stats for the central dashboard."""
    now = time.time()
    online_cameras = sum(1 for info in active_nodes.values() if (now - info.get("last_heartbeat", 0)) < 12.0)
    db_stats = get_db_stats()
    central_online = any(d.is_connected for d in dispatchers.values()) if dispatchers else False
    return {
        "active_cameras": online_cameras,
        "unique_verified": db_stats["unique_verified"],
        "total_sightings": db_stats["total_sightings"],
        "central_receiver_online": central_online
    }


@app.get("/api/central-events")
async def get_central_events(limit: int = 50, student_id: Optional[str] = None, deduplicate: bool = False):
    """
    Directly queries the centralized SQLite database (central_storage/events.db)
    to show sightings collected by the central server.
    If deduplicate=True, groups by student_id to show each verified student uniquely once.
    """
    db_path = config.CENTRAL_STORAGE_DIR / "events.db"
    if not db_path.exists():
        return []

    try:
        with closing(sqlite3.connect(db_path, timeout=5.0)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if deduplicate:
                query = """
                    SELECT student_id, student_name, camera_id, camera_location, status,
                           MIN(observed_at) as first_seen,
                           MAX(observed_at) as last_seen,
                           COUNT(*) as sightings_count,
                           MAX(confidence) as confidence
                    FROM sightings
                    WHERE status = 'VERIFIED'
                """
                params = []
                if student_id:
                    query += " AND (student_id LIKE ? OR student_name LIKE ?)"
                    params.extend([f"%{student_id}%", f"%{student_id}%"])
                query += " GROUP BY student_id ORDER BY last_seen DESC LIMIT ?"
                params.append(limit)
                cursor.execute(query, tuple(params))
            else:
                if student_id:
                    cursor.execute("""
                        SELECT event_id, student_id, student_name, status, camera_id, camera_location,
                               confidence, observed_at, received_at, latency_ms
                        FROM sightings
                        WHERE student_id LIKE ? OR student_name LIKE ?
                        ORDER BY observed_at DESC
                        LIMIT ?
                    """, (f"%{student_id}%", f"%{student_id}%", limit))
                else:
                    cursor.execute("""
                        SELECT event_id, student_id, student_name, status, camera_id, camera_location,
                               confidence, observed_at, received_at, latency_ms
                        FROM sightings
                        ORDER BY observed_at DESC
                        LIMIT ?
                    """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error querying events.db: {e}")
        return []


class EnrollRequest(BaseModel):
    student_id: str
    name: str
    image_base64: str
    department: Optional[str] = "Engineering"
    batch: Optional[str] = "2026"


@app.post("/api/enroll-face")
async def enroll_face(req: EnrollRequest):
    """Enrolls a student face directly into the biometric database."""
    global student_db
    clean_id = req.student_id.strip()
    clean_name = req.name.strip()
    if not clean_id or not clean_name:
        raise HTTPException(status_code=400, detail="Student ID and Name are required.")

    try:
        raw_b64 = req.image_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(raw_b64)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Failed to decode image bytes")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image format: {e}")

    meta = {
        "student_id": clean_id,
        "name": clean_name,
        "department": req.department,
        "batch": req.batch,
        "enrolled_at": datetime.datetime.now().astimezone().isoformat(),
        "enrollment_source": "Camera Node Web Station"
    }

    success = student_db.register_new_student(clean_id, img, metadata=meta)
    if not success:
        raise HTTPException(status_code=400, detail="No face detected in the frame.")

    logger.info(f"Student [{clean_id}] '{clean_name}' enrolled successfully.")
    await broadcast_to_dashboards("STUDENT_ENROLLED", {
        "student_id": clean_id,
        "name": clean_name,
        "enrolled_count": student_db.get_enrolled_count()
    })

    return {
        "success": True,
        "message": f"Student {clean_name} ({clean_id}) enrolled successfully!",
        "enrolled_count": student_db.get_enrolled_count()
    }


@app.post("/api/reload-db")
async def reload_database():
    """Forces re-indexing of the database directory to detect newly added photos or angles."""
    global student_db
    if student_db:
        student_db.reload()
        return {
            "success": True,
            "message": "Student database reloaded successfully",
            "enrolled_count": student_db.get_enrolled_count()
        }
    return {"success": False, "message": "Database not initialized"}


# -----------------------------------------------------------------------------
# WebSocket: Centralized Dashboard Live Bus
# -----------------------------------------------------------------------------
@app.websocket("/ws/dashboard")
async def dashboard_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    dashboard_sockets.add(websocket)
    logger.info("Centralized Dashboard WebSocket connected.")

    # Send initial state snapshot
    db_stats = get_db_stats()
    await websocket.send_json({
        "type": "SNAPSHOT",
        "nodes": list(active_nodes.values()),
        "trajectories": list(student_trajectories.values()),
        "unique_verified": db_stats["unique_verified"],
        "total_sightings": db_stats["total_sightings"]
    })

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        dashboard_sockets.discard(websocket)
        logger.info("Centralized Dashboard WebSocket disconnected.")
    except Exception:
        dashboard_sockets.discard(websocket)


# -----------------------------------------------------------------------------
# WebSocket: Real-Time Edge Camera Feed & Inference Loop
# -----------------------------------------------------------------------------
@app.websocket("/ws/camera-feed")
async def camera_feed_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_ip = websocket.client.host if websocket.client else "127.0.0.1"

    camera_id = config.CAMERA_ID
    camera_location = config.CAMERA_LOCATION
    camera_sockets[camera_id] = websocket
    fps_time = time.perf_counter()
    fps_counter = 0
    frame_broadcast_counter = 0
    measured_fps = 0.0

    logger.info(f"Camera Node connected from {client_ip}.")

    try:
        while True:
            try:
                data_text = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                break

            msg = json.loads(data_text)
            m_type = msg.get("type", "FRAME")

            if m_type == "CONFIG":
                camera_id = msg.get("camera_id", camera_id)
                camera_location = msg.get("camera_location", camera_location)
                camera_sockets[camera_id] = websocket
                get_or_create_dispatcher(camera_id, camera_location)

                active_nodes[camera_id] = {
                    "camera_id": camera_id,
                    "location": camera_location,
                    "ip_address": client_ip,
                    "online": True,
                    "last_heartbeat": time.time(),
                    "fps": measured_fps,
                    "latency_ms": 0.0,
                    "total_sightings": 0,
                    "latest_thumbnail": None
                }

                await websocket.send_json({
                    "type": "CONFIG_ACK",
                    "camera_id": camera_id,
                    "camera_location": camera_location,
                    "connected_nodes": [
                        {"camera_id": k, "location": v.get("location"), "online": (time.time() - v.get("last_heartbeat", 0)) < 12.0}
                        for k, v in active_nodes.items()
                    ]
                })

                await broadcast_to_dashboards("NODE_UPDATED", active_nodes[camera_id])
                continue

            if m_type == "FRAME":
                b64_frame = msg.get("frame", "")
                client_cam_id = msg.get("camera_id", camera_id)
                client_location = msg.get("camera_location", camera_location)

                if "," in b64_frame:
                    b64_frame = b64_frame.split(",", 1)[1]

                frame_bytes = base64.b64decode(b64_frame)
                np_arr = np.frombuffer(frame_bytes, np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if frame is None:
                    await websocket.send_json({
                        "type": "FRAME_RESULT",
                        "fps": round(measured_fps, 1),
                        "latency_ms": 0.0,
                        "active_faces": 0,
                        "recognitions": [],
                        "data_sent_receipts": [],
                        "connected_nodes": [
                            {"camera_id": k, "location": v.get("location"), "online": (time.time() - v.get("last_heartbeat", 0)) < 12.0}
                            for k, v in active_nodes.items()
                        ]
                    })
                    continue

                t_start = time.perf_counter()

                # 1. Face Detection
                t_d0 = time.perf_counter()
                faces = detector.detect(frame)
                t_detect = (time.perf_counter() - t_d0) * 1000

                dispatcher = get_or_create_dispatcher(client_cam_id, client_location)
                recognitions = []
                data_sent_receipts = []

                for face in faces:
                    t_r0 = time.perf_counter()
                    # 2. Extract 128-d SFace embedding
                    embedding = recognizer.extract_embedding(frame, face["raw"])
                    # 3. Vectorized Match
                    stu_id, status, sim_score, margin, stu_meta = student_db.match(embedding)
                    t_rec = (time.perf_counter() - t_r0) * 1000
                    face_latency = t_detect + t_rec

                    student_name = stu_meta.get("name", stu_id) if stu_meta else stu_id

                    rec_entry = {
                        "bbox": face["bbox"],
                        "student_id": stu_id,
                        "student_name": student_name,
                        "status": status,
                        "confidence": round(float(sim_score), 4),
                        "margin": round(float(margin), 4),
                        "latency_ms": round(face_latency, 2),
                        "landmarks": face["landmarks"],
                        "metadata": stu_meta
                    }
                    recognitions.append(rec_entry)

                    # Trigger sighting for recognized students
                    if status in ("VERIFIED", "UNCERTAIN"):
                        disp_res = dispatcher.trigger_sighting(
                            student_id=stu_id,
                            status=status,
                            confidence=sim_score,
                            bbox=face["bbox"],
                            latency_ms=face_latency,
                            margin=margin,
                            student_info=stu_meta
                        )

                        now_ts = datetime.datetime.now().astimezone()
                        now_str = now_ts.strftime("%H:%M:%S")

                        # Create receipt for "Data Sent Logs"
                        receipt = {
                            "event_id": str(uuid.uuid4())[:8],
                            "timestamp": now_str,
                            "student_id": stu_id,
                            "student_name": student_name,
                            "camera_id": client_cam_id,
                            "target": "Central Server (ws://127.0.0.1:8765)",
                            "latency_ms": round(face_latency, 1),
                            "confidence": round(float(sim_score) * 100, 1),
                            "status": "DISPATCHED" if dispatcher.is_connected else "QUEUED_LOCAL"
                        }
                        data_sent_receipts.append(receipt)

                        # Update in-memory sighting record
                        sighting_rec = {
                            "timestamp": now_str,
                            "camera_id": client_cam_id,
                            "location": client_location,
                            "student_id": stu_id,
                            "student_name": student_name,
                            "status": status,
                            "confidence": round(float(sim_score) * 100, 1),
                            "margin": round(float(margin) * 100, 1),
                            "latency_ms": round(face_latency, 1)
                        }

                        if not recent_sightings or recent_sightings[-1].get("student_id") != stu_id or (time.time() - recent_sightings[-1].get("_t", 0) > 3.0):
                            sighting_rec["_t"] = time.time()
                            recent_sightings.append(sighting_rec)
                            if len(recent_sightings) > 50:
                                recent_sightings.pop(0)

                        # Update cross-camera trajectory STRICTLY for VERIFIED students
                        if status == "VERIFIED" and stu_id and stu_id != "UNKNOWN":
                            if stu_id in student_trajectories:
                                traj = student_trajectories[stu_id]
                                if traj["current_camera"] != client_cam_id:
                                    transit_time = time.time() - traj["last_seen_epoch"]
                                    traj["trajectory"].append({
                                        "from_camera": traj["current_camera"],
                                        "to_camera": client_cam_id,
                                        "location": client_location,
                                        "transit_sec": round(transit_time, 1),
                                        "time": now_str
                                    })
                                    traj["current_camera"] = client_cam_id
                                    traj["current_location"] = client_location
                                traj["last_seen_epoch"] = time.time()
                                traj["last_seen"] = now_str
                                traj["confidence"] = round(float(sim_score) * 100, 1)
                            else:
                                student_trajectories[stu_id] = {
                                    "student_id": stu_id,
                                    "student_name": student_name,
                                    "current_camera": client_cam_id,
                                    "current_location": client_location,
                                    "first_seen": now_str,
                                    "last_seen": now_str,
                                    "last_seen_epoch": time.time(),
                                    "confidence": round(float(sim_score) * 100, 1),
                                    "trajectory": [{
                                        "from_camera": "START",
                                        "to_camera": client_cam_id,
                                        "location": client_location,
                                        "transit_sec": 0.0,
                                        "time": now_str
                                    }]
                                }

                        # Broadcast to dashboard
                        await broadcast_to_dashboards("NEW_SIGHTING", {
                            "sighting": sighting_rec,
                            "trajectory": student_trajectories.get(stu_id) if status == "VERIFIED" and stu_id != "UNKNOWN" else None
                        })

                # Measure throughput
                total_latency = (time.perf_counter() - t_start) * 1000
                fps_counter += 1
                if time.perf_counter() - fps_time >= 1.0:
                    measured_fps = fps_counter / (time.perf_counter() - fps_time)
                    fps_counter = 0
                    fps_time = time.perf_counter()

                # Visualizer rendering
                rendered = visualizer.render(
                    frame=frame,
                    recognitions=recognitions,
                    fps=measured_fps,
                    latency_ms=total_latency,
                    enrolled_count=student_db.get_enrolled_count(),
                    last_event_student=dispatcher.last_dispatched_student,
                    last_event_time=dispatcher.last_dispatched_time
                )

                # Generate clean preview thumbnail for Central Dashboard Multi-Camera Matrix
                h, w = rendered.shape[:2]
                thumb_w = 320
                thumb_h = int(320 * h / w)
                thumb = cv2.resize(rendered, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
                _, thumb_buf = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 65])
                thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(thumb_buf).decode("ascii")

                # Strictly filter verified students for dashboard map & telemetry
                verified_students = [
                    {
                        "student_id": r["student_id"],
                        "student_name": r["student_name"],
                        "confidence": round(float(r["confidence"]), 4),
                        "status": r["status"]
                    }
                    for r in recognitions
                    if r.get("student_id") and r.get("student_id") != "UNKNOWN" and r.get("status") == "VERIFIED"
                ]

                # Update active node registry
                active_nodes[client_cam_id] = {
                    "camera_id": client_cam_id,
                    "location": client_location,
                    "ip_address": client_ip,
                    "online": True,
                    "last_heartbeat": time.time(),
                    "fps": round(measured_fps, 1),
                    "latency_ms": round(total_latency, 1),
                    "latest_thumbnail": thumb_b64,
                    "total_sightings": dispatcher.total_dispatched,
                    "active_faces": len(faces),
                    "active_students": verified_students,
                    "latest_student": verified_students[0]["student_id"] if verified_students else None,
                    "latest_name": verified_students[0]["student_name"] if verified_students else None,
                    "latest_confidence": verified_students[0]["confidence"] if verified_students else 0.0,
                    "latest_status": "VERIFIED" if verified_students else None
                }

                # Broadcast live camera stream directly to the Centralized Dashboard
                frame_broadcast_counter += 1
                if frame_broadcast_counter % 2 == 0 and dashboard_sockets:
                    await broadcast_to_dashboards("NODE_FRAME", active_nodes[client_cam_id])

                _, out_buf = cv2.imencode(".jpg", rendered, [cv2.IMWRITE_JPEG_QUALITY, 80])
                out_b64 = base64.b64encode(out_buf).decode("ascii")

                # Send frame result and receipts back to camera client
                await websocket.send_json({
                    "type": "FRAME_RESULT",
                    "frame": "data:image/jpeg;base64," + out_b64,
                    "fps": round(measured_fps, 1),
                    "latency_ms": round(total_latency, 1),
                    "active_faces": len(faces),
                    "recognitions": recognitions,
                    "enrolled_count": student_db.get_enrolled_count(),
                    "data_sent_receipts": data_sent_receipts,
                    "central_receiver_online": dispatcher.is_connected,
                    "connected_nodes": [
                        {"camera_id": k, "location": v.get("location"), "online": (time.time() - v.get("last_heartbeat", 0)) < 12.0}
                        for k, v in active_nodes.items()
                    ]
                })

    except WebSocketDisconnect:
        logger.info(f"Camera Node [{camera_id}] disconnected.")
    except Exception as e:
        logger.error(f"Error in camera feed loop: {e}")
    finally:
        camera_sockets.pop(camera_id, None)
        if camera_id in active_nodes:
            active_nodes[camera_id]["online"] = False
        await broadcast_to_dashboards("NODE_OFFLINE", {"camera_id": camera_id})


# Mount static assets
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def run_dual_servers():
    """Runs HTTP (:8500) and HTTPS (:8443) concurrently for mobile camera compatibility."""
    cert_path = PROJECT_ROOT / "certs" / "cert.pem"
    key_path = PROJECT_ROOT / "certs" / "key.pem"

    if cert_path.exists() and key_path.exists():
        logger.info("SSL certificates found. Launching Dual HTTP (:8500) & HTTPS (:8443) servers...")
        cfg_http = uvicorn.Config("web_node.server:app", host="0.0.0.0", port=8500, log_level="info")
        cfg_https = uvicorn.Config(
            "web_node.server:app",
            host="0.0.0.0",
            port=8443,
            ssl_certfile=str(cert_path),
            ssl_keyfile=str(key_path),
            log_level="info"
        )
        srv_http = uvicorn.Server(cfg_http)
        srv_https = uvicorn.Server(cfg_https)

        async def serve():
            await asyncio.gather(srv_http.serve(), srv_https.serve())

        asyncio.run(serve())
    else:
        logger.info("Launching standard HTTP (:8500) server...")
        uvicorn.run("web_node.server:app", host="0.0.0.0", port=8500, reload=False)


if __name__ == "__main__":
    run_dual_servers()
