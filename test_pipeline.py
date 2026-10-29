"""
End-to-End Test Suite for Distributed Architecture, Ambiguity Calibration & Central Store.
"""
import time
import json
import uuid
import shutil
import asyncio
import threading
from pathlib import Path
import cv2
import numpy as np

import config
from core.detector import FaceDetector
from core.recognizer import FaceRecognizer
from core.student_db import StudentDatabase
from core.event_dispatcher import CameraEventDispatcher
from central_server import CentralServer, CentralEventStore

def run_tests():
    print("=" * 70)
    print("RUNNING DISTRIBUTED PIPELINE & CALIBRATION VERIFICATION")
    print("=" * 70)

    # 1. Test Model Initialization
    print("\n[Test 1] Testing Detector and Recognizer initialization...")
    detector = FaceDetector(config.DETECTOR_MODEL_PATH)
    recognizer = FaceRecognizer(config.RECOGNIZER_MODEL_PATH)
    assert detector is not None
    assert recognizer is not None
    print("  --> Models loaded successfully!")

    # 2. Test Face Detection
    print("\n[Test 2] Testing Face Detection on enrolled image...")
    sample_img_path = Path("database/2410719/front.jpg")
    img = cv2.imread(str(sample_img_path))
    assert img is not None, f"Sample image not found at {sample_img_path}"
    faces = detector.detect(img)
    assert len(faces) > 0, "Failed to detect face in sample image"
    print(f"  --> Detected {len(faces)} face. Box: {faces[0]['bbox']}, Confidence: {faces[0]['confidence']:.4f}")

    # 3. Test Calibrated Ambiguity & Margin Matching
    print("\n[Test 3] Testing Calibrated Ambiguity & Margin Matching...")
    test_db_dir = Path("/tmp/test_calib_db")
    if test_db_dir.exists():
        shutil.rmtree(test_db_dir)
    test_db_dir.mkdir(parents=True, exist_ok=True)
    
    # Enroll test student 2410719
    s_dir = test_db_dir / "2410719"
    s_dir.mkdir(parents=True)
    shutil.copy(str(sample_img_path), s_dir / "front.jpg")
    with open(s_dir / "info.json", "w") as f:
        json.dump({"student_id": "2410719", "name": "Pranjal yadav"}, f)

    db = StudentDatabase(
        test_db_dir, detector, recognizer,
        confirmation_threshold=0.45,
        ambiguity_threshold=0.36,
        margin_threshold=0.06
    )
    assert db.get_enrolled_count() == 1

    # Identical match
    query_feat = recognizer.extract_embedding(img, faces[0]["raw"])
    cand_id, status, score, margin, meta = db.match(query_feat)
    print(f"  --> Identical Query: ID={cand_id}, Status={status}, Score={score:.4f}, Margin={margin:.4f}")
    assert status == "VERIFIED", f"Expected VERIFIED, got {status}"
    assert cand_id == "2410719"

    # Pure noise match
    noise_feat = np.random.randn(1, 128).astype(np.float32)
    noise_feat /= np.linalg.norm(noise_feat)
    _, noise_status, noise_score, _, _ = db.match(noise_feat)
    print(f"  --> Random Noise Query: Status={noise_status}, Score={noise_score:.4f}")
    assert noise_status == "UNKNOWN", f"Expected UNKNOWN, got {noise_status}"
    print("  --> Margin & Ambiguity calibration verified!")

    # 4. Test Central Event Server, Auth Handshake, SQLite Persistence & Deduplication
    print("\n[Test 4] Testing Central Server Authentication, Deduplication & Storage...")
    test_storage = Path("/tmp/test_central_storage")
    if test_storage.exists():
        shutil.rmtree(test_storage)
    test_storage.mkdir(parents=True, exist_ok=True)

    server = CentralServer(
        host="127.0.0.1",
        port=9988,
        auth_token="test-secret-token",
        storage_dir=test_storage
    )

    def run_server():
        asyncio.run(server.start())

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(0.5)

    # Initialize Camera Node Dispatcher with local queue
    test_queue_db = Path("/tmp/test_cam_queue.db")
    if test_queue_db.exists():
        test_queue_db.unlink()

    dispatcher = CameraEventDispatcher(
        endpoint_url="ws://127.0.0.1:9988",
        camera_id="CAM-TEST-GATE",
        camera_location="North Gate",
        auth_token="test-secret-token",
        cooldown_seconds=1.0,
        local_queue_db=test_queue_db
    )
    dispatcher.start()

    # Trigger sighting 1
    t_start = time.time()
    dispatched = dispatcher.trigger_sighting(
        student_id="2410719",
        status="VERIFIED",
        confidence=score,
        bbox=faces[0]["bbox"],
        latency_ms=22.5,
        margin=margin,
        student_info={"name": "Pranjal yadav"}
    )
    assert dispatched is True, "First sighting must be queued"

    # Wait for delivery and ACK
    time.sleep(1.0)
    dispatcher.stop()

    # Verify storage in Central SQLite & JSONL
    store = CentralEventStore(test_storage)
    sightings = store.query_trajectory("2410719")
    print(f"  --> Sightings persisted in Central Store: {len(sightings)}")
    assert len(sightings) == 1, f"Expected 1 persisted sighting, got {len(sightings)}"
    first_record = sightings[0]
    print(f"      Camera ID   : {first_record['camera_id']} ({first_record['camera_location']})")
    print(f"      Observed At : {first_record['observed_at']}")
    print(f"      Received At : {first_record['received_at']}")
    assert first_record["camera_id"] == "CAM-TEST-GATE"
    assert first_record["status"] == "VERIFIED"
    assert "received_at" in first_record and "observed_at" in first_record

    # Verify Deduplication: Insert same record again directly to store
    is_new = store.persist(json.loads(first_record["raw_payload"]), received_at="2026-10-29T13:30:00Z")
    assert is_new is False, "Duplicate event_id must be rejected"
    print("  --> Idempotent deduplication verified!")

    # 5. Multi-Camera Trajectory Query
    print("\n[Test 5] Testing Multi-Camera Trajectory Query...")
    # Simulate a second sighting from Library camera
    evt2 = {
        "event_id": str(uuid.uuid4()),
        "student_id": "2410719",
        "student_name": "Pranjal yadav",
        "status": "VERIFIED",
        "camera_id": "CAM-LIBRARY-01",
        "camera_location": "Central Library",
        "observed_at": "2026-10-29T14:15:00+05:30",
        "confidence": 0.89,
        "bbox": {"x": 100, "y": 80, "width": 120, "height": 140},
        "inference_latency_ms": 18.0
    }
    store.persist(evt2, received_at="2026-10-29T14:15:01+05:30")

    full_traj = store.query_trajectory("2410719")
    assert len(full_traj) == 2, f"Expected 2 sightings across cameras, got {len(full_traj)}"
    print("  --> Successfully reconstructed multi-camera trajectory:")
    for idx, s in enumerate(full_traj, 1):
        print(f"      {idx}. {s['observed_at']} @ [{s['camera_id']}] {s['camera_location']} ({s['confidence']*100:.1f}%)")

    # Clean up test files
    shutil.rmtree(test_db_dir, ignore_errors=True)
    shutil.rmtree(test_storage, ignore_errors=True)
    if test_queue_db.exists():
        test_queue_db.unlink()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 70)

if __name__ == "__main__":
    run_tests()
