"""
Student Enrollment Utility.
Enroll students via webcam capture or by importing an image file.
"""
import sys
import argparse
from pathlib import Path
import cv2

import config
from core.detector import FaceDetector
from core.recognizer import FaceRecognizer
from core.student_db import StudentDatabase

def enroll_from_image(db: StudentDatabase, student_id: str, image_path: str, name: str = None):
    p = Path(image_path)
    if not p.exists():
        print(f"[!] Error: Image file '{image_path}' not found.")
        return False
        
    img = cv2.imread(str(p))
    if img is None:
        print(f"[!] Error: Failed to decode image '{image_path}'.")
        return False

    meta = {"name": name or student_id}
    success = db.register_new_student(student_id, img, meta)
    if success:
        print(f"[+] Successfully enrolled student '{student_id}' ({meta['name']}).")
        print(f"    Saved into: {db.db_dir / f'{student_id}.jpg'}")
    else:
        print(f"[!] Failed: No detectable face found in '{image_path}'.")
    return success

def enroll_from_camera(db: StudentDatabase, detector: FaceDetector, student_id: str, 
                       cam_index: int = 0, name: str = None):
    print(f"\n[*] Opening camera {cam_index} for enrollment...")
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        print(f"[!] Could not open camera {cam_index}.")
        return False

    print("\n---------------------------------------------------------")
    print(f" Enrolling Student: ID='{student_id}', Name='{name or student_id}'")
    print(" Position your face inside the green box.")
    print(" Press SPACE to capture face.")
    print(" Press ESC / 'q' to cancel.")
    print("---------------------------------------------------------")

    window_title = f"Enroll Student - {student_id}"
    captured = False

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        display = frame.copy()
        faces = detector.detect(frame)

        if faces:
            x, y, w, h = faces[0]["bbox"]
            cv2.rectangle(display, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(display, f"Face Detected ({faces[0]['confidence']:.2f}) - Press SPACE", 
                        (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        else:
            cv2.putText(display, "No Face Detected - Look directly at camera", 
                        (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        cv2.imshow(window_title, display)
        key = cv2.waitKey(1) & 0xFF
        if key == 27 or key == ord("q"):
            print("[*] Enrollment cancelled by user.")
            break
        elif key == 32: # SPACE
            if not faces:
                print("[!] Cannot capture: No face detected.")
                continue
            meta = {"name": name or student_id}
            success = db.register_new_student(student_id, frame, meta)
            if success:
                print(f"[+] Student '{student_id}' captured and enrolled successfully!")
                captured = True
            else:
                print("[!] Face verification failed during enrollment.")
            break

    cap.release()
    cv2.destroyAllWindows()
    return captured

def main():
    parser = argparse.ArgumentParser(description="Enroll student faces into the recognition database.")
    parser.add_argument("--id", required=True, help="Fixed Student ID (e.g., STU101, 2024CS042)")
    parser.add_argument("--name", default=None, help="Full Student Name (optional)")
    parser.add_argument("--image", default=None, help="Path to existing image file to import")
    parser.add_argument("--cam", type=int, default=config.CAMERA_INDEX, help="Camera index for live capture")

    args = parser.parse_args()

    detector = FaceDetector(config.DETECTOR_MODEL_PATH)
    recognizer = FaceRecognizer(config.RECOGNIZER_MODEL_PATH)
    db = StudentDatabase(config.DATABASE_DIR, detector, recognizer, config.CACHE_FILE)

    if args.image:
        enroll_from_image(db, args.id, args.image, args.name)
    else:
        enroll_from_camera(db, detector, args.id, args.cam, args.name)

if __name__ == "__main__":
    main()
