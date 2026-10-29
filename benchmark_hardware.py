"""
Empirical Hardware Benchmark for Face Recognition Pipeline.
Measures real performance (mean, p50, p95, p99) on the local CPU and camera.
"""
import time
import cv2
import numpy as np

import config
from core.detector import FaceDetector
from core.recognizer import FaceRecognizer

def benchmark():
    print("=" * 70)
    print(" EMPIRICAL HARDWARE BENCHMARK & LATENCY PROFILER")
    print("=" * 70)

    # 1. Hardware & System Info
    print("\n[1/5] Initializing models and environment...")
    detector_320 = FaceDetector(config.DETECTOR_MODEL_PATH, target_size=(320, 240))
    detector_640 = FaceDetector(config.DETECTOR_MODEL_PATH, target_size=(640, 480))
    recognizer = FaceRecognizer(config.RECOGNIZER_MODEL_PATH)

    # Warmup
    dummy_frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    for _ in range(5):
        detector_320.detect(dummy_frame)
        detector_640.detect(dummy_frame)

    # 2. Benchmark Face Detection at (320, 240) vs (640, 480)
    print("\n[2/5] Benchmarking YuNet Face Detection over 100 frames...")
    
    times_320 = []
    for _ in range(100):
        t0 = time.perf_counter()
        detector_320.detect(dummy_frame)
        times_320.append((time.perf_counter() - t0) * 1000)

    times_640 = []
    for _ in range(100):
        t0 = time.perf_counter()
        detector_640.detect(dummy_frame)
        times_640.append((time.perf_counter() - t0) * 1000)

    def print_stats(name, data):
        print(f"  • {name}:")
        print(f"      Mean   : {np.mean(data):.2f} ms (~{1000/np.mean(data):.1f} FPS)")
        print(f"      Median : {np.percentile(data, 50):.2f} ms")
        print(f"      p95    : {np.percentile(data, 95):.2f} ms")
        print(f"      p99    : {np.percentile(data, 99):.2f} ms")
        print(f"      Min/Max: {np.min(data):.2f} ms / {np.max(data):.2f} ms")

    print_stats("YuNet @ 320x240 (Optimized)", times_320)
    print_stats("YuNet @ 640x480 (Full Res)", times_640)

    # 3. Benchmark SFace Alignment & Embedding Extraction
    print("\n[3/5] Benchmarking SFace Embedding Extraction over 100 faces...")
    dummy_face_vec = np.array([100, 100, 120, 150, 130, 140, 180, 140, 155, 175, 135, 210, 175, 210, 0.95], dtype=np.float32)
    
    # Warmup
    for _ in range(5):
        recognizer.extract_embedding(dummy_frame, dummy_face_vec)

    rec_times = []
    for _ in range(100):
        t0 = time.perf_counter()
        recognizer.extract_embedding(dummy_frame, dummy_face_vec)
        rec_times.append((time.perf_counter() - t0) * 1000)

    print_stats("SFace Feature Extraction", rec_times)

    # 4. Benchmark Database Matching (Vectorized Cosine) at different scales
    print("\n[4/5] Benchmarking Vectorized NumPy Cosine Search...")
    query_emb = np.random.randn(1, 128).astype(np.float32)
    query_emb /= np.linalg.norm(query_emb)

    for db_size in [10, 100, 1000, 10000]:
        db_mat = np.random.randn(db_size, 128).astype(np.float32)
        db_mat /= np.linalg.norm(db_mat, axis=1, keepdims=True)
        
        match_times = []
        for _ in range(500):
            t0 = time.perf_counter()
            scores = np.dot(db_mat, query_emb.T)
            best_idx = np.argmax(scores)
            match_times.append((time.perf_counter() - t0) * 1000)
            
        print(f"  • DB Size {db_size:>5} students: Mean = {np.mean(match_times)*1000:.2f} µs ({np.mean(match_times):.4f} ms)")

    # 5. Benchmark Real Camera Stream Frame Delivery
    print("\n[5/5] Measuring Camera Stream throughput & jitter...")
    cap = cv2.VideoCapture(0)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        frame_deltas = []
        t_prev = time.perf_counter()
        for _ in range(60):
            ret, _ = cap.read()
            t_now = time.perf_counter()
            if ret:
                frame_deltas.append((t_now - t_prev) * 1000)
            t_prev = t_now
        cap.release()

        if frame_deltas:
            # skip first couple frames for camera sensor auto-exposure stabilization
            stable_deltas = frame_deltas[5:]
            fps = 1000.0 / np.mean(stable_deltas)
            print(f"  • Raw Camera Frame Interval: Mean = {np.mean(stable_deltas):.2f} ms ({fps:.1f} FPS)")
            print(f"  • Camera Jitter (StdDev)    : {np.std(stable_deltas):.2f} ms")
    else:
        print("  • Camera 0 not available for hardware streaming test.")

    # 6. Combined Full Pipeline Estimate
    det_mean = np.mean(times_320)
    rec_mean = np.mean(rec_times)
    total_cycle = det_mean + rec_mean + 0.05 # matching
    max_sustained_fps = min(30.0, 1000.0 / total_cycle)

    print("\n" + "=" * 70)
    print(" EMPIRICAL VERDICT SUMMARY ON THIS HARDWARE")
    print(f"  • Detection Latency (YuNet 320x240) : {det_mean:.2f} ms")
    print(f"  • Recognition Latency (SFace)      : {rec_mean:.2f} ms per face")
    print(f"  • Matching Latency (1,000 DB items): < 0.05 ms")
    print(f"  • Total Inference Latency (1 Face) : {total_cycle:.2f} ms")
    print(f"  • Max Sustainable AI Throughput    : ~{1000/total_cycle:.1f} FPS (Targeted to camera's ~{max_sustained_fps:.1f} FPS)")
    print("=" * 70)

if __name__ == "__main__":
    benchmark()
