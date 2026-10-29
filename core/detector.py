"""
YuNet Real-Time Face Detector wrapper using OpenCV DNN.
"""
import cv2
import numpy as np
import logging

logger = logging.getLogger("FaceDetector")

class FaceDetector:
    """
    Lightweight, ultra-fast face detector powered by YuNet.
    Runs at 50-100+ FPS on CPU.
    """
    def __init__(self, model_path: str, conf_threshold: float = 0.75, nms_threshold: float = 0.30, 
                 target_size: tuple = (320, 240)):
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold
        self.target_size = target_size # (width, height) for detector input
        
        self.detector = cv2.FaceDetectorYN.create(
            model=self.model_path,
            config="",
            input_size=self.target_size,
            score_threshold=self.conf_threshold,
            nms_threshold=self.nms_threshold,
            top_k=1000
        )
        logger.info(f"YuNet FaceDetector initialized with input size {self.target_size}")

    def detect(self, frame: np.ndarray):
        """
        Detects faces in frame.
        Returns a list of face dictionaries:
        [{
            'bbox': (x, y, w, h),
            'confidence': float,
            'raw': numpy array (15 elements formatted for SFace alignCrop),
            'landmarks': [(x, y), ...]
        }]
        """
        orig_h, orig_w = frame.shape[:2]
        target_w, target_h = self.target_size
        
        # Resize frame for ultra-fast detection if needed
        if (orig_w, orig_h) != (target_w, target_h):
            small_frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
            sx = orig_w / float(target_w)
            sy = orig_h / float(target_h)
        else:
            small_frame = frame
            sx, sy = 1.0, 1.0

        self.detector.setInputSize((target_w, target_h))
        _, raw_faces = self.detector.detect(small_frame)

        if raw_faces is None or len(raw_faces) == 0:
            return []

        results = []
        for face_vec in raw_faces:
            # face_vec format: [x, y, w, h, x_re, y_re, x_le, y_le, x_nt, y_nt, x_rc, y_rc, x_lc, y_lc, score]
            scaled_vec = face_vec.copy()
            
            # Scale coordinates back to original frame size
            scaled_vec[0] = face_vec[0] * sx # x
            scaled_vec[1] = face_vec[1] * sy # y
            scaled_vec[2] = face_vec[2] * sx # w
            scaled_vec[3] = face_vec[3] * sy # h
            
            # Landmarks (5 points: 10 coords)
            for i in range(4, 14, 2):
                scaled_vec[i] = face_vec[i] * sx
                scaled_vec[i + 1] = face_vec[i + 1] * sy
                
            x, y, w, h = int(scaled_vec[0]), int(scaled_vec[1]), int(scaled_vec[2]), int(scaled_vec[3])
            
            # Clamp to frame boundaries
            x = max(0, x)
            y = max(0, y)
            w = min(orig_w - x, max(1, w))
            h = min(orig_h - y, max(1, h))
            conf = float(face_vec[14])
            
            landmarks = [
                (int(scaled_vec[4]), int(scaled_vec[5])),   # Right eye
                (int(scaled_vec[6]), int(scaled_vec[7])),   # Left eye
                (int(scaled_vec[8]), int(scaled_vec[9])),   # Nose tip
                (int(scaled_vec[10]), int(scaled_vec[11])), # Right mouth corner
                (int(scaled_vec[12]), int(scaled_vec[13]))  # Left mouth corner
            ]

            results.append({
                "bbox": (x, y, w, h),
                "confidence": conf,
                "raw": scaled_vec,
                "landmarks": landmarks
            })

        return results
