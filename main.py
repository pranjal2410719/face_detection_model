"""
Main Application: Robust Edge Camera Node for Face Recognition.
Identifies enrolled students locally, displays calibrated HUD with uncertainty states,
and sends authenticated, idempotent sightings to the Central Server.
"""
import sys
import time
import argparse
import logging
from pathlib import Path
import cv2
import numpy as np

import config
from core.camera_stream import CameraStream
from core.detector import FaceDetector
from core.recognizer import FaceRecognizer
from core.student_db import StudentDatabase
from core.event_dispatcher import CameraEventDispatcher
from core.visualizer import Visualizer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [CameraNode] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("CameraNode")

def parse_args():
    parser = argparse.ArgumentParser(description="Edge Camera Node for Face Recognition")
    parser.add_argument("--camera", type=int, default=config.CAMERA_INDEX, help="Camera device index (default: 0)")
    parser.add_argument("--endpoint", default=config.EVENT_ENDPOINT_URL, help="Central WebSocket endpoint URL")
    parser.add_argument("--cam-id", default=config.CAMERA_ID, help="Camera Identifier (e.g., CAM-GATE-01)")
    parser.add_argument("--location", default=config.CAMERA_LOCATION, help="Camera location description")
    parser.add_argument("--token", default=config.CAMERA_AUTH_TOKEN, help="Authentication token for central server")
    parser.add_argument("--conf-threshold", type=float, default=config.CONFIRMATION_THRESHOLD,
                        help="Confidence threshold for VERIFIED status (default: 0.45)")
    parser.add_argument("--ambig-threshold", type=float, default=config.AMBIGUITY_THRESHOLD,
                        help="Confidence threshold for UNCERTAIN status (default: 0.36)")
    parser.add_argument("--margin-threshold", type=float, default=config.MARGIN_THRESHOLD,
                        help="Required score margin between 1st and 2nd best candidate (default: 0.06)")
    parser.add_argument("--cooldown", type=float, default=config.EVENT_COOLDOWN_SECONDS,
                        help="Event trigger cooldown in seconds per student (default: 5.0)")
    parser.add_argument("--headless", action="store_true", help="Run without UI window (headless server mode)")
    parser.add_argument("--fps", type=int, default=config.TARGET_FPS, help="Target camera FPS (default: 30)")
    parser.add_argument("--width", type=int, default=config.FRAME_WIDTH, help="Camera width (default: 640)")
    parser.add_argument("--height", type=int, default=config.FRAME_HEIGHT, help="Camera height (default: 480)")
    return parser.parse_args()

def main():
    args = parse_args()
    logger.info("Initializing Edge Camera Node...")

    # 1. Initialize Detector and Recognizer models
    if not Path(config.DETECTOR_MODEL_PATH).exists() or not Path(config.RECOGNIZER_MODEL_PATH).exists():
        logger.error("Model files missing in models/ directory. Run download script.")
        sys.exit(1)

    detector = FaceDetector(
        model_path=config.DETECTOR_MODEL_PATH,
        conf_threshold=config.DETECTION_CONF_THRESHOLD,
        nms_threshold=config.DETECTION_NMS_THRESHOLD,
        target_size=(320, 240) # Highly optimized for CPU real-time detection
    )
    recognizer = FaceRecognizer(model_path=config.RECOGNIZER_MODEL_PATH)

    # 2. Initialize Student Database with Calibrated Ambiguity & Margin Thresholds
    student_db = StudentDatabase(
        db_dir=config.DATABASE_DIR,
        detector=detector,
        recognizer=recognizer,
        cache_path=config.CACHE_FILE,
        confirmation_threshold=args.conf_threshold,
        ambiguity_threshold=args.ambig_threshold,
        margin_threshold=args.margin_threshold
    )
    enrolled_count = student_db.get_enrolled_count()
    logger.info(f"Student Database loaded with {enrolled_count} registered students.")

    # 3. Initialize Resilient Camera Event Dispatcher (Local Queue + Auth + Retry)
    event_dispatcher = CameraEventDispatcher(
        endpoint_url=args.endpoint,
        camera_id=args.cam_id,
        camera_location=args.location,
        auth_token=args.token,
        cooldown_seconds=args.cooldown
    )
    event_dispatcher.start()

    # 4. Initialize Threaded Zero-Lag Camera Stream
    logger.info(f"Connecting to camera index {args.camera}...")
    try:
        camera = CameraStream(
            src=args.camera,
            width=args.width,
            height=args.height,
            target_fps=args.fps
        ).start()
    except Exception as e:
        logger.error(f"Failed to start camera stream: {e}")
        event_dispatcher.stop()
        sys.exit(1)

    # 5. Initialize Visualizer
    visualizer = Visualizer(camera_id=args.cam_id, show_landmarks=config.SHOW_LANDMARKS)

    logger.info("=" * 65)
    logger.info(" Edge Camera Node Online & Running!")
    logger.info(f"  • Camera ID          : {args.cam_id}")
    logger.info(f"  • Physical Location  : {args.location}")
    logger.info(f"  • Central Endpoint   : {args.endpoint}")
    logger.info(f"  • Enrolled Count     : {enrolled_count}")
    logger.info(f"  • Verify Threshold   : >= {args.conf_threshold} (Margin >= {args.margin_threshold})")
    logger.info(f"  • Ambiguity Threshold: {args.ambig_threshold} - {args.conf_threshold}")
    logger.info(f"  • Mode               : {'Headless' if args.headless else 'Interactive GUI'}")
    logger.info(" Controls: [Q/ESC] Quit  |  [R] Reload DB  |  [L] Landmarks  |  [S] Snapshot")
    logger.info("=" * 65)

    # FPS and performance tracking
    fps_history = []
    fps_time = time.perf_counter()
    measured_fps = 0.0

    try:
        while True:
            loop_start = time.perf_counter()
            ret, frame, capture_ts = camera.read()
            if not ret or frame is None:
                time.sleep(0.005)
                continue

            # Run Face Detection
            t_detect_start = time.perf_counter()
            faces = detector.detect(frame)
            t_detect_end = time.perf_counter()

            recognitions = []
            # Process detected faces
            for face in faces:
                t_rec_start = time.perf_counter()
                
                # Extract 128-d feature vector using SFace
                embedding = recognizer.extract_embedding(frame, face["raw"])
                
                # Compare against enrolled database with calibrated margin & ambiguity checks
                student_id, status, sim_score, margin, student_meta = student_db.match(embedding)
                t_rec_end = time.perf_counter()

                # Total latency for this face = detection time + recognition time
                face_latency_ms = ((t_detect_end - t_detect_start) + (t_rec_end - t_rec_start)) * 1000

                rec_entry = {
                    "bbox": face["bbox"],
                    "student_id": student_id,
                    "status": status, # "VERIFIED", "UNCERTAIN", or "UNKNOWN"
                    "confidence": sim_score,
                    "margin": margin,
                    "landmarks": face["landmarks"],
                    "metadata": student_meta,
                    "latency_ms": face_latency_ms
                }
                recognitions.append(rec_entry)

                # Trigger sighting for VERIFIED or UNCERTAIN detections
                if status in ("VERIFIED", "UNCERTAIN"):
                    event_dispatcher.trigger_sighting(
                        student_id=student_id,
                        status=status,
                        confidence=sim_score,
                        bbox=face["bbox"],
                        latency_ms=face_latency_ms,
                        margin=margin,
                        student_info=student_meta
                    )

            # Calculate total frame cycle latency and FPS
            loop_duration = time.perf_counter() - loop_start
            total_latency_ms = loop_duration * 1000
            
            fps_history.append(loop_duration)
            if len(fps_history) > 30:
                fps_history.pop(0)
            if time.perf_counter() - fps_time >= 0.5:
                measured_fps = len(fps_history) / sum(fps_history) if fps_history else 0.0
                fps_time = time.perf_counter()

            # Render UI
            if not args.headless:
                annotated_frame = visualizer.render(
                    frame=frame,
                    recognitions=recognitions,
                    fps=measured_fps,
                    latency_ms=total_latency_ms,
                    enrolled_count=student_db.get_enrolled_count(),
                    last_event_student=event_dispatcher.last_dispatched_student,
                    last_event_time=event_dispatcher.last_dispatched_time
                )

                cv2.imshow(config.WINDOW_TITLE, annotated_frame)
                key = cv2.waitKey(1) & 0xFF

                if key == 27 or key == ord("q"):
                    logger.info("Exit requested by user.")
                    break
                elif key == ord("r"):
                    logger.info("Reloading student database from disk...")
                    student_db.build_or_load_index(force_rebuild=True)
                elif key == ord("l"):
                    visualizer.show_landmarks = not visualizer.show_landmarks
                    logger.info(f"Landmarks display toggled to: {visualizer.show_landmarks}")
                elif key == ord("s"):
                    snap_dir = config.BASE_DIR / "snapshots"
                    snap_dir.mkdir(exist_ok=True)
                    snap_file = snap_dir / f"snapshot_{int(time.time())}.jpg"
                    cv2.imwrite(str(snap_file), annotated_frame)
                    logger.info(f"Saved snapshot: {snap_file}")
            else:
                # Headless pacing
                time.sleep(0.001)

    except KeyboardInterrupt:
        logger.info("Interrupted by keyboard.")
    finally:
        logger.info("Shutting down camera node...")
        camera.stop()
        event_dispatcher.stop()
        cv2.destroyAllWindows()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    main()
