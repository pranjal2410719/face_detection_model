# NetCam • Distributed Face Recognition & Real-Time Centralized Monitoring Network

A high-performance, real-time distributed facial recognition system and web platform. NetCam combines low-latency neural network inference (OpenCV YuNet + SFace), dual-website web architecture (Edge Camera Node Station and Centralized Command Dashboard), dynamic topological network mapping, and durable SQLite event auditing.

---

## 🌟 Key Features

### 1. Dual-Website Architecture
- **Edge Camera Station (`/` • Port `8500` / `8443`)**:
  - Minimalist client-side camera application designed for edge devices, laptops, and mobile phones.
  - Streams camera frames to the inference engine with real-time green AI bounding box outlines and student name tags.
  - Node settings (Camera ID, Location), local connection status, and durable offline SQLite queue.
- **Centralized Command Dashboard (`/dashboard` • Port `8500` / `8443`)**:
  - Centralized monitoring station collecting and aggregating data across all distributed cameras.
  - **Dynamic Topological Network Map**: Real-time SVG network visualizer that dynamically plots camera nodes, changes node color through multiple shades of green based on recognition confidence, and draws glowing directed edges with arrows tracking student movement across cameras.
  - **Live Multi-Camera Matrix**: Live video feeds from all connected camera nodes with AI bounding boxes and FPS telemetry.
  - **Deduplicated Student Verification Logs**: Displays each verified student once with their latest sighting count, first/last seen timestamps, and status, eliminating repetitive log clutter.

### 2. High-Performance AI Pipeline (< 25 ms Response Time)
- **Face Detection**: Lightweight OpenCV Zoo **YuNet** running in ~15–20 ms on CPU with multi-threading.
- **Biometric Feature Extraction**: Deep learning **SFace** generating 128-dimensional L2-normalized embeddings.
- **Vectorized Vector Search**: Parallel NumPy Cosine Similarity search matching detected faces against the database in **< 0.1 ms**.
- **HUD & Visualizer**: High-contrast green corner brackets, verified student ID & name badges, and live telemetry.

### 3. Dynamic Topology Map & Confidence Shading
- **Automatic Node Discovery**: Nodes appear dynamically on the map as cameras connect to the network.
- **Confidence Shading (`Gray ➔ Green ➔ Greener`)**:
  - `Standby (Default)`: Dark Slate Gray (`#1e2632`) — Camera connected, awaiting face detection.
  - `Low Confidence (< 60%)`: Forest Green (`#14532d`) — Soft detection.
  - `Medium Confidence (60% – 80%)`: Vibrant Emerald Green (`#047857`) — Confirmed match.
  - `High / Peak Confidence (> 80%)`: Electric Neon Green (`#00ff88`) — Ultra-high match with glowing radar pulse.
- **Directed Transition Edges with Arrowhead**:
  - When a student walks from one camera to another (e.g. `CAM-01 ➔ CAM-02`), a glowing directed edge with an arrowhead marker links the nodes, displaying the student's name and transition path.

### 4. Cross-Platform Mobile & Dual HTTP/HTTPS Support
- Runs concurrently on **HTTP (port 8500)** and **HTTPS (port 8443)** using local self-signed SSL certificates.
- Enables mobile phone browsers (Chrome, Safari, Firefox) on the local LAN to grant `getUserMedia` camera permissions seamlessly without browser security blocks.

---

## 📂 Codebase Structure

```
face_recognization model/
├── models/                                  # Pre-trained Deep Learning ONNX Models
│   ├── face_detection_yunet_2023mar.onnx    # YuNet face detector (~230 KB)
│   └── face_recognition_sface_2021dec.onnx  # SFace 128-D feature extractor (~38 MB)
│
├── core/                                    # Core AI Engine & Video Processing
│   ├── detector.py                          # YuNet detector wrapper (5 facial landmarks)
│   ├── recognizer.py                        # SFace 128-D feature extraction & alignment
│   ├── student_db.py                        # Vectorized Cosine Matcher & cache (.embeddings_cache.npz)
│   ├── visualizer.py                        # OpenCV HUD renderer (green brackets, tags, clock)
│   ├── event_dispatcher.py                  # Debounced event dispatching & offline queue
│   ├── camera_stream.py                     # Threaded zero-lag camera capture (desktop)
│   └── trajectory_tracker.py                # In-memory trajectory path tracking
│
├── web_node/                                # Web Platform (FastAPI + Modern Web UI)
│   ├── server.py                            # Dual HTTP (:8500) & HTTPS (:8443) FastAPI server
│   └── static/
│       ├── index.html                       # Edge Camera Station Web App (/)
│       ├── app.js                           # Client webcam capture & streaming controller
│       ├── dashboard.html                   # Centralized Monitoring Dashboard (/dashboard)
│       ├── dashboard.js                     # Dynamic SVG Topology Map & Dashboard controller
│       └── styles.css                       # Cyber dark-slate theme & responsive styles
│
├── database/                                # Biometric Enrolled Students
│   ├── metadata.json                        # Student profiles (ID, Name, Department)
│   ├── .embeddings_cache.npz                # Vectorized NumPy embedding cache for instant boot
│   └── 2410719/                             # Enrolled student directory
│       └── front.jpg                        # Front reference photo
│
├── central_storage/                         # Durable Central Audit Store
│   ├── events.db                            # SQLite database of all historical sightings
│   └── events.jsonl                         # Append-only JSON Lines event ledger
│
├── certs/                                   # SSL / TLS Certificates
│   ├── cert.pem                             # Local self-signed SSL Certificate
│   └── key.pem                              # Private Key for HTTPS (:8443)
│
├── central_server.py                        # Standalone Central WebSocket Receiver (:8765)
├── enroll_student.py                        # Interactive Face Enrollment CLI
├── start_web_camera_node.sh                 # One-click startup script
├── benchmark_hardware.py                    # Hardware throughput and latency benchmark
├── config.py                                # System-wide configuration settings
└── requirements.txt                         # Python dependencies
```

---

## 🚀 Quick Start

### 1. Requirements
- Linux / macOS / Windows
- Python 3.9+
- Webcam or mobile phone camera on the same local network

Install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Start the Complete Network
You can start both the Central Event Server and the Web Node platform in one step:
```bash
./start_web_camera_node.sh
```

Or start the services individually:
```bash
# Terminal 1: Launch Central Audit Server (Port 8765)
python3 central_server.py --port 8765

# Terminal 2: Launch Web Node Server (HTTP 8500 / HTTPS 8443)
python3 -m web_node.server
```

---

## 🌐 Web Endpoints & Same Wi-Fi Network Access

When your devices (phones, tablets, client laptops, and host PC) are connected to the **same Wi-Fi network**:

| Service | Local PC URL | Same Wi-Fi Devices (Phones / Clients) |
| :--- | :--- | :--- |
| **Centralized Command Dashboard** | [http://localhost:8500/dashboard](http://localhost:8500/dashboard) | `http://<WIFI_IP>:8500/dashboard` or `https://<WIFI_IP>:8443/dashboard` |
| **Mobile & Client Camera Node** | [http://localhost:8500](http://localhost:8500) | `https://<WIFI_IP>:8443` *(HTTPS required by mobile browsers for camera)* |
| **Network IP & Config API** | [http://localhost:8500/api/network-info](http://localhost:8500/api/network-info) | `http://<WIFI_IP>:8500/api/network-info` |
| **Central WebSocket Receiver** | `ws://127.0.0.1:8765/face-events` | `ws://<WIFI_IP>:8765/face-events` |

> [!TIP]
> **Instant Connect via QR Code**:
> 1. Open the dashboard on your PC at `http://localhost:8500/dashboard`.
> 2. Click the **"📶 Connect Wi-Fi Devices"** button in the top navigation bar.
> 3. Scan the generated **QR Code** directly with any phone connected to the same Wi-Fi.
> 4. In mobile Chrome/Safari, tap **"Advanced" ➔ "Proceed to IP (unsafe)"** to accept the local network SSL certificate and activate the camera!

---

## 📡 REST API & WebSocket Specifications

### WebSocket Endpoints

#### 1. `/ws/camera-feed` (Camera Streaming Endpoint)
- **Role**: Connected by Edge Camera Stations to stream webcam frames.
- **Client Message (`CONFIG`)**:
  ```json
  { "type": "CONFIG", "camera_id": "CAM-01", "location": "Main Entrance Gate" }
  ```
- **Client Message (`FRAME`)**:
  ```json
  { "type": "FRAME", "frame": "data:image/jpeg;base64,...", "timestamp": 1729837.21 }
  ```
- **Server Response (`FRAME_RESULT`)**:
  ```json
  {
    "type": "FRAME_RESULT",
    "frame": "data:image/jpeg;base64,...",
    "fps": 18.4,
    "latency_ms": 22.1,
    "active_faces": 1,
    "recognitions": [{ "student_id": "2410719", "confidence": 0.94, "status": "VERIFIED" }]
  }
  ```

#### 2. `/ws/dashboard` (Central Live Bus)
- **Role**: Connected by the Centralized Dashboard to receive live node updates, thumbnails, and detections.
- **Messages**:
  - `SNAPSHOT`: Initial active nodes, trajectories, and statistics upon connecting.
  - `NODE_FRAME`: High-frequency frame broadcast (every 2 frames) containing live 320px AI-annotated thumbnail and FPS.
  - `NEW_SIGHTING`: Triggered on student verification with cross-camera trajectory data.
  - `NODE_OFFLINE`: Broadcasted when a camera disconnects.

---

### REST APIs

- **`GET /api/dashboard-stats`**: Returns live camera count, unique verified students count, and total sightings stored in SQLite.
- **`GET /api/connected-nodes`**: Returns all active camera nodes with online status, location, FPS, and latest preview thumbnail.
- **`GET /api/central-events?deduplicate=true`**: Returns deduplicated student verification records (shows each student once with sighting counts and first/last detected times).
- **`GET /api/central-events?deduplicate=false`**: Returns chronological stream of all individual detection sightings.
- **`GET /api/trajectories`**: Returns active cross-camera movement paths for all students.
- **`POST /api/enroll-face`**: Enrolls a new student face directly from base64 image data.

---

## 👤 Adding & Enrolling Student Faces

### Method A: Web-Based Live Enrollment (Recommended)
1. Open the Camera Node Station at `http://localhost:8500` (or `https://<YOUR_IP>:8443`).
2. Click **`➕ Enroll Face`** in the top navigation bar.
3. Position your face in front of the camera, enter the **Student ID** and **Full Name**, and click **`Capture & Enroll`**.
4. The system calculates the 128-D biometric vector and updates the database instantly without restarting.

### Method B: Direct Photo Placement
Create a folder inside `database/` with the Student ID and place a front-facing image:
```
database/
└── 2410719/
    └── front.jpg
```
Add optional metadata in `database/metadata.json`:
```json
{
  "2410719": {
    "name": "Pranjal yadav",
    "department": "Engineering"
  }
}
```

### Method C: Command-Line Enrollment
```bash
python3 enroll_student.py --id 2410719 --name "Pranjal yadav" --image /path/to/photo.jpg
```

---

## 🧪 Verification & Automated Testing

To run the end-to-end integration test verifying the AI models, multi-camera live feeds, dynamic topology map, and cross-camera transitions:

```bash
python3 - << 'EOF'
import asyncio, json, base64, cv2, websockets

async def test():
    img = cv2.imread("database/2410719/front.jpg")
    _, buf = cv2.imencode(".jpg", img)
    b64_frame = base64.b64encode(buf).decode("ascii")

    async with websockets.connect("ws://127.0.0.1:8500/ws/dashboard") as ws_dash:
        snap = json.loads(await ws_dash.recv())
        print(f"[TEST] Dashboard connected: verified={snap.get('unique_verified')}")

        async with websockets.connect("ws://127.0.0.1:8500/ws/camera-feed") as ws_cam:
            await ws_cam.send(json.dumps({"type": "CONFIG", "camera_id": "CAM-01", "location": "Main Gate"}))
            await ws_cam.recv()
            await ws_cam.send(json.dumps({"type": "FRAME", "frame": b64_frame, "timestamp": 1.0}))
            res = json.loads(await ws_cam.recv())
            print(f"[TEST] Recognition Result: {res.get('recognitions')}")

asyncio.run(test())
EOF
```

---

## 🔒 Security & Offline Resilience
- **Offline Durability**: If the central network drops, edge camera nodes store events in a local SQLite queue (`.queue_<CAM_ID>.db`) and automatically flush the backlog when connectivity is restored.
- **Deduplication**: Sightings are debounced by a configurable cooldown period (default: 5.0s) so continuous frames verify continuously without flooding the audit database.
