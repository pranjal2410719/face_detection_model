"""
SFace Face Recognition Feature Extractor using OpenCV DNN.
"""
import cv2
import numpy as np
import logging

logger = logging.getLogger("FaceRecognizer")

class FaceRecognizer:
    """
    SFace Deep Feature Extractor.
    Extracts 128-dimensional L2-normalized embeddings for fast cosine similarity comparison.
    """
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.recognizer = cv2.FaceRecognizerSF.create(model=self.model_path, config="")
        logger.info("SFace FaceRecognizer initialized.")

    def extract_embedding(self, frame: np.ndarray, face_raw: np.ndarray) -> np.ndarray:
        """
        Aligns the face using detected facial landmarks and extracts a 128-d feature vector.
        Returns a normalized (1, 128) float32 numpy vector.
        """
        # Align and crop face to standard 112x112 input
        aligned_face = self.recognizer.alignCrop(frame, face_raw)
        
        # Extract 128-d feature
        feature = self.recognizer.feature(aligned_face)
        
        # L2-normalize feature vector
        norm = np.linalg.norm(feature)
        if norm > 1e-6:
            feature = feature / norm
            
        return feature.astype(np.float32)

    def compute_similarity(self, feat1: np.ndarray, feat2: np.ndarray) -> float:
        """
        Computes cosine similarity between two normalized feature vectors.
        Returns score between -1.0 and 1.0.
        """
        return float(np.dot(feat1.flatten(), feat2.flatten()))
