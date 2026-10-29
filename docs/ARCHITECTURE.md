# NetCam • System Architecture & Engineering Documentation

This document provides a comprehensive technical overview of the NetCam distributed face recognition and monitoring architecture.

---

## 1. High-Level Architecture Overview

NetCam is designed as a modular, distributed edge-to-cloud/central system:

```
[ Client Camera Node 1 ] ──(WebSocket /ws/camera-feed)──┐
  - Browser Webcam Capture                              │
  - Local Preview HUD                                   │
                                                        ▼
[ Client Camera Node 2 ] ──(WebSocket /ws/camera-feed)──► [ Web Node Server (:8500/:8443) ]
  - Mobile Phone (HTTPS)                                │   - YuNet Detection (320x240)
                                                        │   - SFace Biometric Extraction
                                                        │   - Vectorized Cosine Match
                                                        │   - Dual-frame Thumbnail Gen
                                                        │
                                                        ├─► [ Centralized Dashboard (:8500/dashboard) ]
                                                        │     - Dynamic SVG Network Map
                                                        │     - Confidence Shading (Gray->Green)
                                                        │     - Directed Transition Arrows
                                                        │     - Live Multi-Camera Matrix
                                                        │     - Deduplicated Attendance Log
                                                        │
                                                        ▼
                                                  [ Central Audit Server (:8765) ]
                                                      - events.db (SQLite)
                                                      - events.jsonl
```

---

## 2. Deep Learning & Computer Vision Subsystem

### 2.1 Face Detection (YuNet)
- **Model**: `models/face_detection_yunet_2023mar.onnx`
- **Input Size**: Configurable, default `320 x 240` (optimized for sub-20ms latency on CPU).
- **Features**: Outputs bounding box `[x, y, w, h]`, detection score, and 5 facial landmark keypoints (right eye, left eye, nose tip, right mouth corner, left mouth corner).
- **Implementation**: [`core/detector.py`](file:///home/dev/projects/face_recognization%20model/core/detector.py)

### 2.2 Biometric Feature Extraction (SFace)
- **Model**: `models/face_recognition_sface_2021dec.onnx`
- **Output**: 128-dimensional L2-normalized floating-point embedding vector.
- **Warping & Alignment**: Performs 5-point affine transformation aligning eye and mouth landmarks to standard coordinate space before feature extraction.
- **Implementation**: [`core/recognizer.py`](file:///home/dev/projects/face_recognization%20model/core/recognizer.py)

### 2.3 Vectorized Matching & Embedding Cache
- **Matcher**: Vectorized matrix dot product against all enrolled student embeddings:
  $$\text{Similarity}(E_{\text{query}}, E_{\text{db}}) = E_{\text{query}} \cdot E_{\text{db}}^T$$
- **Threshold Calibration**:
  - `Similarity >= 0.363`: Candidate match
  - `Margin >= 0.05` (difference between top-1 and top-2 match): `VERIFIED`
  - Otherwise: `UNCERTAIN`
- **Cache**: Precomputed embeddings are persisted in `database/.embeddings_cache.npz` with MD5 fingerprinting, ensuring instant server startup without re-running inference on student photos.
- **Implementation**: [`core/student_db.py`](file:///home/dev/projects/face_recognization%20model/core/student_db.py)

---

## 3. Web & Network Subsystem

### 3.1 Dual-Port HTTP/HTTPS Serving
- **HTTP (Port `8500`)**: For desktop browsers, API calls, and local loopback.
- **HTTPS (Port `8443`)**: Uses self-signed SSL certificates ([`certs/cert.pem`](file:///home/dev/projects/face_recognization%20model/certs/cert.pem) and [`certs/key.pem`](file:///home/dev/projects/face_recognization%20model/certs/key.pem)). Required by mobile web browsers (Safari on iOS, Chrome on Android) to permit `navigator.mediaDevices.getUserMedia` video capture across local WiFi.
- **Implementation**: [`web_node/server.py`](file:///home/dev/projects/face_recognization%20model/web_node/server.py)

### 3.2 Real-Time WebSocket Protocols
1. **`/ws/camera-feed`**:
   - Transmits base64 JPEG video frames from camera nodes to the server.
   - Server runs detector, recognizer, and HUD visualizer, returning base64 annotated frames with bounding boxes back to the client.
2. **`/ws/dashboard`**:
   - Pushes live camera thumbnails (broadcast every 2 frames at 320px width) directly to active dashboards.
   - Pushes `NEW_SIGHTING` events containing student IDs, confidence scores, and cross-camera trajectories.

---

## 4. Dynamic Topological Network Map

The Central Dashboard features a reactive SVG visualizer:

### 4.1 Node Coordinate Calculation
When $N$ camera nodes are connected, coordinates are distributed along an SVG canvas (`1000 x 340` viewBox):
- $N = 1$: Center $(500, 170)$
- $N = 2$: $(280, 170)$ and $(720, 170)$
- $N \ge 3$: Evenly spaced across the X-axis with alternating vertical offsets ($\pm 25\text{px}$) to prevent visual crowding.

### 4.2 Confidence Color Gradient
Nodes dynamically change fill and stroke colors based on facial recognition confidence:
- **Standby**: `#1e2632` (Gray)
- **Low Confidence (< 60%)**: `#14532d` (Forest Green)
- **Medium Confidence (60%–80%)**: `#047857` (Emerald Green)
- **High / Peak Confidence (> 80%)**: `#00ff88` (Electric Neon Green) with radial pulse waves (`@keyframes radarWave`).

### 4.3 Directed Movement Trajectories
When student $S$ appears at Camera $A$ and subsequently appears at Camera $B$:
- An SVG quadratic bezier curve path is generated from Node $A$ to Node $B$:
  $$M(x_1, y_1) \quad Q(x_{\text{mid}}, y_{\text{mid}} - 35) \quad (x_2, y_2)$$
- Rendered with an arrowhead marker (`marker-end="url(#edgeArrowGreen)"`) and an animated dashed electric flow (`@keyframes flowDash`).
- Tagged with the student's name and transition duration.

---

## 5. Storage & Database Schema

### SQLite Audit Store: `central_storage/events.db`

```sql
CREATE TABLE IF NOT EXISTS sightings (
    event_id         TEXT PRIMARY KEY,
    student_id       TEXT NOT NULL,
    student_name     TEXT,
    status           TEXT NOT NULL,
    camera_id        TEXT NOT NULL,
    camera_location  TEXT NOT NULL,
    confidence       REAL NOT NULL,
    observed_at      TEXT NOT NULL,
    received_at      TEXT NOT NULL,
    bbox_x           INTEGER,
    bbox_y           INTEGER,
    bbox_w           INTEGER,
    bbox_h           INTEGER,
    latency_ms       REAL,
    raw_payload      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sightings_student_id ON sightings(student_id);
CREATE INDEX IF NOT EXISTS idx_sightings_observed_at ON sightings(observed_at);
```

### Deduplicated Attendance Query
To prevent flooding the dashboard with continuous frames of the same student, the server executes:
```sql
SELECT 
    student_id, student_name, camera_id, camera_location, status,
    MIN(observed_at) as first_seen,
    MAX(observed_at) as last_seen,
    COUNT(*) as sightings_count,
    MAX(confidence) as confidence
FROM sightings
WHERE status = 'VERIFIED'
GROUP BY student_id
ORDER BY last_seen DESC;
```
This produces one consolidated, clean row per student.
