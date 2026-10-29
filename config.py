"""
System Configuration for Real-Time Face Recognition & Event Dispatcher.
"""
import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
DATABASE_DIR = BASE_DIR / "database"
CACHE_FILE = DATABASE_DIR / ".embeddings_cache.npz"

# Model Paths (YuNet for ultra-fast face detection & SFace for face recognition)
DETECTOR_MODEL_PATH = str(MODELS_DIR / "face_detection_yunet_2023mar.onnx")
RECOGNIZER_MODEL_PATH = str(MODELS_DIR / "face_recognition_sface_2021dec.onnx")

# Camera Configuration
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
FRAME_WIDTH = int(os.getenv("FRAME_WIDTH", "640"))
FRAME_HEIGHT = int(os.getenv("FRAME_HEIGHT", "480"))
TARGET_FPS = int(os.getenv("TARGET_FPS", "30"))
CAMERA_ID = os.getenv("CAMERA_ID", "CAM-MAIN-01")
CAMERA_LOCATION = os.getenv("CAMERA_LOCATION", "Main Entrance Gate")

# Detection & Recognition Thresholds
DETECTION_CONF_THRESHOLD = float(os.getenv("DETECTION_CONF_THRESHOLD", "0.75"))
DETECTION_NMS_THRESHOLD = float(os.getenv("DETECTION_NMS_THRESHOLD", "0.30"))

# SFace Cosine Similarity Thresholds & Ambiguity Calibration:
# VERIFIED: High confidence and distinct from 2nd best candidate
# UNCERTAIN: Moderate confidence or close margin (ambiguous)
# UNKNOWN: Below ambiguity threshold
CONFIRMATION_THRESHOLD = float(os.getenv("CONFIRMATION_THRESHOLD", "0.45"))
AMBIGUITY_THRESHOLD = float(os.getenv("AMBIGUITY_THRESHOLD", "0.36"))
MARGIN_THRESHOLD = float(os.getenv("MARGIN_THRESHOLD", "0.06"))
UNKNOWN_LABEL = "UNKNOWN"
UNCERTAIN_LABEL = "UNCERTAIN"

# Central Server Configuration
CENTRAL_SERVER_HOST = os.getenv("CENTRAL_SERVER_HOST", "0.0.0.0")
CENTRAL_SERVER_PORT = int(os.getenv("CENTRAL_SERVER_PORT", "8765"))
CENTRAL_STORAGE_DIR = BASE_DIR / "central_storage"
CAMERA_AUTH_TOKEN = os.getenv("CAMERA_AUTH_TOKEN", "cam-secret-token-key-2026")

# Real-Time Event Dispatcher Configuration (WebSocket & HTTP)
# Can be ws:// or http://. Default points to local event receiver.
EVENT_ENDPOINT_URL = os.getenv("EVENT_ENDPOINT_URL", "ws://127.0.0.1:8765/face-events")

# Event cooldown in seconds: avoids flooding events while a recognized student remains in camera view
EVENT_COOLDOWN_SECONDS = float(os.getenv("EVENT_COOLDOWN_SECONDS", "5.0"))

# UI Display Configuration
WINDOW_TITLE = "Real-Time Student Face Recognition System"
SHOW_FPS = True
SHOW_LANDMARKS = False
DISPLAY_SCALE = float(os.getenv("DISPLAY_SCALE", "1.0"))
