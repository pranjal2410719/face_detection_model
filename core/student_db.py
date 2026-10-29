"""
Student Database Manager with Vectorized Cosine Matching and Caching.
"""
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np

from core.detector import FaceDetector
from core.recognizer import FaceRecognizer

logger = logging.getLogger("StudentDB")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

class StudentDatabase:
    """
    Manages student face embeddings and metadata.
    Enables ultra-fast vectorized cosine matching against all enrolled students.
    """
    def __init__(self, db_dir: str or Path, detector: FaceDetector, recognizer: FaceRecognizer,
                 cache_path: Optional[str or Path] = None, 
                 confirmation_threshold: float = 0.45,
                 ambiguity_threshold: float = 0.36,
                 margin_threshold: float = 0.06):
        self.db_dir = Path(db_dir)
        self.detector = detector
        self.recognizer = recognizer
        self.cache_path = Path(cache_path) if cache_path else self.db_dir / ".embeddings_cache.npz"
        self.confirmation_threshold = confirmation_threshold
        self.ambiguity_threshold = ambiguity_threshold
        self.margin_threshold = margin_threshold
        
        # Student metadata {student_id: dict}
        self.metadata: Dict[str, dict] = {}
        # List of student IDs corresponding to each row in embeddings_matrix
        self.enrolled_ids: List[str] = []
        # Matrix of shape (N, 128)
        self.embeddings_matrix: Optional[np.ndarray] = None
        
        self.load_metadata()
        self.build_or_load_index()

    def load_metadata(self):
        """
        Loads metadata from both:
          1. Individual student folders: database/{student_id}/info.json (or profile.json)
          2. Root database/metadata.json (fallback)
        """
        self.metadata = {}
        
        # 1. First check global metadata.json
        meta_file = self.db_dir / "metadata.json"
        if not meta_file.exists():
            meta_file = self.db_dir / "students.json"
            
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    self.metadata.update(json.load(f))
            except Exception as e:
                logger.warning(f"Failed to read root metadata file: {e}")

        # 2. Check per-student directories for info.json or profile.json (takes precedence)
        if self.db_dir.exists():
            for entry in self.db_dir.iterdir():
                if entry.is_dir() and not entry.name.startswith("."):
                    student_id = entry.name
                    for info_candidate in ["info.json", "profile.json", "student.json", "metadata.json"]:
                        info_file = entry / info_candidate
                        if info_file.exists():
                            try:
                                with open(info_file, "r", encoding="utf-8") as f:
                                    student_info = json.load(f)
                                self.metadata[student_id] = student_info
                                break
                            except Exception as e:
                                logger.warning(f"Failed to read {info_file}: {e}")

        logger.info(f"Loaded student profile metadata for {len(self.metadata)} students.")

    def save_metadata(self):
        """Saves current metadata to metadata.json."""
        meta_file = self.db_dir / "metadata.json"
        try:
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")

    def build_or_load_index(self, force_rebuild: bool = False):
        """
        Scans database folder and builds or loads embeddings cache.
        """
        self.db_dir.mkdir(parents=True, exist_ok=True)
        image_files = self._collect_image_files()
        
        if not image_files:
            logger.warning(f"No student face images found in database directory: {self.db_dir}")
            self.enrolled_ids = []
            self.embeddings_matrix = None
            return

        # Check if cache is valid
        if not force_rebuild and self.cache_path.exists():
            cached_data = self._load_cache(image_files)
            if cached_data is not None:
                self.enrolled_ids, self.embeddings_matrix = cached_data
                logger.info(f"Loaded {len(self.enrolled_ids)} face embeddings from cache.")
                return

        logger.info(f"Indexing {len(image_files)} student images from {self.db_dir}...")
        enrolled_ids = []
        embeddings_list = []

        for student_id, img_path in image_files:
            embedding = self._extract_from_image(img_path)
            if embedding is not None:
                enrolled_ids.append(student_id)
                embeddings_list.append(embedding.flatten())
                logger.info(f"Enrolled student: {student_id} from {img_path.name}")
            else:
                logger.warning(f"No face detected in database image: {img_path}")

        if embeddings_list:
            self.enrolled_ids = enrolled_ids
            self.embeddings_matrix = np.array(embeddings_list, dtype=np.float32)
            # Save cache
            self._save_cache(image_files, self.enrolled_ids, self.embeddings_matrix)
            logger.info(f"Successfully indexed and cached {len(self.enrolled_ids)} student faces.")
        else:
            self.enrolled_ids = []
            self.embeddings_matrix = None
            logger.warning("No valid faces were detected in the database folder.")

    def _collect_image_files(self) -> List[Tuple[str, Path]]:
        """
        Scans db_dir for images.
        Supports both:
          1. Directly placed images: database/{student_id}.jpg
          2. Folders per student: database/{student_id}/front.jpg or database/{student_id}/photos/front.jpg
             (Excludes tracking/ folder)
        """
        results = []
        for entry in sorted(self.db_dir.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
                student_id = entry.stem
                results.append((student_id, entry))
            elif entry.is_dir():
                student_id = entry.name
                for sub_entry in sorted(entry.iterdir()):
                    # Skip tracking directory
                    if sub_entry.name.lower() in {"tracking", "trajectories", ".git"}:
                        continue
                    if sub_entry.is_file() and sub_entry.suffix.lower() in IMAGE_EXTENSIONS:
                        results.append((student_id, sub_entry))
                    elif sub_entry.is_dir() and sub_entry.name.lower() in {"photos", "images", "faces"}:
                        for photo in sorted(sub_entry.iterdir()):
                            if photo.is_file() and photo.suffix.lower() in IMAGE_EXTENSIONS:
                                results.append((student_id, photo))
        return results

    def _extract_from_image(self, img_path: Path) -> Optional[np.ndarray]:
        """Loads an image, detects the face, and returns its 128-d embedding."""
        img = cv2.imread(str(img_path))
        if img is None:
            logger.error(f"Could not read image file: {img_path}")
            return None

        # Detect face
        faces = self.detector.detect(img)
        if not faces:
            # Try at original image size if small size missed it
            temp_detector = cv2.FaceDetectorYN.create(
                model=self.detector.model_path,
                config="",
                input_size=(img.shape[1], img.shape[0]),
                score_threshold=0.50
            )
            _, raw_faces = temp_detector.detect(img)
            if raw_faces is None or len(raw_faces) == 0:
                return None
            best_face = raw_faces[0]
        else:
            best_face = faces[0]["raw"]

        # Extract embedding
        return self.recognizer.extract_embedding(img, best_face)

    def _save_cache(self, image_files: List[Tuple[str, Path]], enrolled_ids: List[str], matrix: np.ndarray):
        """Saves embeddings and file hashes/timestamps to cache."""
        try:
            file_meta = {str(p): p.stat().st_mtime for _, p in image_files}
            np.savez(
                self.cache_path,
                ids=np.array(enrolled_ids, dtype=object),
                embeddings=matrix,
                file_meta=json.dumps(file_meta)
            )
        except Exception as e:
            logger.warning(f"Could not save embeddings cache: {e}")

    def _load_cache(self, image_files: List[Tuple[str, Path]]) -> Optional[Tuple[List[str], np.ndarray]]:
        """Validates and loads embeddings cache."""
        try:
            data = np.load(self.cache_path, allow_pickle=True)
            cached_meta = json.loads(str(data["file_meta"]))
            current_meta = {str(p): p.stat().st_mtime for _, p in image_files}
            
            # Check if any files were added, deleted, or modified
            if cached_meta != current_meta:
                logger.info("Database files changed, updating cache...")
                return None
                
            enrolled_ids = list(data["ids"])
            matrix = data["embeddings"]
            return enrolled_ids, matrix
        except Exception as e:
            logger.debug(f"Cache miss or error: {e}")
            return None

    def match(self, query_embedding: np.ndarray) -> Tuple[str, str, float, float, Optional[dict]]:
        """
        Matches a query face embedding against all enrolled students using
        vectorized cosine similarity with margin and ambiguity checks.
        
        Returns:
            (student_id, status, best_score, margin, metadata_dict)
            status can be:
              - "VERIFIED" : High confidence and distinct from 2nd best candidate
              - "UNCERTAIN": Meets minimum similarity but margin is narrow or in ambiguity zone
              - "UNKNOWN"  : Below ambiguity threshold
        """
        if self.embeddings_matrix is None or len(self.enrolled_ids) == 0:
            return "UNKNOWN", "UNKNOWN", 0.0, 0.0, None

        # Fast vectorized matrix dot product: (N, 128) x (128, 1) -> (N,)
        scores = np.dot(self.embeddings_matrix, query_embedding.flatten())
        
        if len(scores) == 1:
            best_idx = 0
            best_score = float(scores[0])
            margin = 1.0 # only 1 enrolled candidate, margin is maximal
        else:
            sorted_indices = np.argsort(scores)[::-1]
            best_idx = int(sorted_indices[0])
            best_score = float(scores[best_idx])
            second_score = float(scores[sorted_indices[1]])
            margin = best_score - second_score

        candidate_id = self.enrolled_ids[best_idx]
        student_info = self.metadata.get(candidate_id, {"name": candidate_id})

        # Classification decision logic
        if best_score >= self.confirmation_threshold and margin >= self.margin_threshold:
            return candidate_id, "VERIFIED", best_score, margin, student_info
        elif best_score >= self.ambiguity_threshold:
            # Ambiguous: could be the candidate, but certainty is insufficient
            return candidate_id, "UNCERTAIN", best_score, margin, student_info
        else:
            return "UNKNOWN", "UNKNOWN", best_score, margin, None

    def get_enrolled_count(self) -> int:
        """Returns the number of unique registered students."""
        return len(set(self.enrolled_ids))

    def register_new_student(self, student_id: str, image: np.ndarray, 
                             metadata: Optional[dict] = None) -> bool:
        """
        Enrolls a new student image directly into database/{student_id}/, saves
        photo and info.json, and updates current in-memory index immediately.
        """
        student_dir = self.db_dir / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        
        faces = self.detector.detect(image)
        if not faces:
            logger.error("No face detected in enrollment frame.")
            return False

        save_path = student_dir / "front.jpg"
        embedding = self.recognizer.extract_embedding(image, faces[0]["raw"])
        cv2.imwrite(str(save_path), image)

        # Save student profile into info.json
        info_file = student_dir / "info.json"
        student_info = metadata or {"student_id": student_id, "name": student_id}
        student_info["student_id"] = student_id
        try:
            with open(info_file, "w", encoding="utf-8") as f:
                json.dump(student_info, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to write {info_file}: {e}")

        self.metadata[student_id] = student_info

        # Update in-memory index
        self.enrolled_ids.append(student_id)
        if self.embeddings_matrix is None:
            self.embeddings_matrix = np.array([embedding.flatten()], dtype=np.float32)
        else:
            self.embeddings_matrix = np.vstack([self.embeddings_matrix, embedding.flatten()])

        # Update cache
        image_files = self._collect_image_files()
        self._save_cache(image_files, self.enrolled_ids, self.embeddings_matrix)
        logger.info(f"Student '{student_id}' registered successfully.")
        return True
