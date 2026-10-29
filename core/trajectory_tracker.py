"""
Student Trajectory & Location Tracking Manager.
Chronologically logs student appearances per camera location with timestamps in JSON format.
"""
import os
import json
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import logging

logger = logging.getLogger("TrajectoryTracker")

class TrajectoryTracker:
    """
    Manages student location trajectories.
    Saves and organizes sightings chronologically in database/{student_id}/tracking/
    """
    def __init__(self, db_dir: str or Path):
        self.db_dir = Path(db_dir)
        self.lock = threading.Lock()

    def _get_student_tracking_dir(self, student_id: str) -> Path:
        """Returns and creates database/{student_id}/tracking/ directory."""
        tracking_dir = self.db_dir / student_id / "tracking"
        tracking_dir.mkdir(parents=True, exist_ok=True)
        return tracking_dir

    def record_sighting(self, student_id: str, student_name: str,
                        camera_id: str, camera_location: str,
                        timestamp: str, confidence: float,
                        bbox: dict, extra_info: Optional[dict] = None) -> dict:
        """
        Records a timestamped sighting of a student at a specific camera location.
        Updates database/{student_id}/tracking/trajectory.json and daily log.
        """
        with self.lock:
            tracking_dir = self._get_student_tracking_dir(student_id)
            trajectory_file = tracking_dir / "trajectory.json"

            # Parse datetime or use current
            try:
                dt = datetime.fromisoformat(timestamp)
            except Exception:
                dt = datetime.now().astimezone()
                timestamp = dt.isoformat()

            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M:%S")

            # Entry record
            sighting_entry = {
                "timestamp": timestamp,
                "date": date_str,
                "time": time_str,
                "camera_id": camera_id,
                "camera_location": camera_location,
                "confidence": round(float(confidence), 4),
                "bbox": bbox,
                "metadata": extra_info or {}
            }

            # 1. Update overall trajectory.json
            data = {
                "student_id": student_id,
                "student_name": student_name,
                "total_sightings": 0,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "last_location": camera_location,
                "trajectory": []
            }

            if trajectory_file.exists():
                try:
                    with open(trajectory_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as e:
                    logger.warning(f"Could not read existing trajectory for {student_id}: {e}")

            data["student_name"] = student_name or data.get("student_name", student_id)
            data["last_seen"] = timestamp
            data["last_location"] = camera_location
            
            # Append and ensure strictly sorted by timestamp
            data["trajectory"].append(sighting_entry)
            data["trajectory"].sort(key=lambda x: x.get("timestamp", ""))
            data["total_sightings"] = len(data["trajectory"])
            if data["trajectory"]:
                data["first_seen"] = data["trajectory"][0]["timestamp"]

            # Save main trajectory.json atomically
            temp_file = tracking_dir / "trajectory.json.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_file.replace(trajectory_file)

            # 2. Update daily log: tracking/YYYY-MM-DD.json
            daily_file = tracking_dir / f"{date_str}.json"
            daily_entries = []
            if daily_file.exists():
                try:
                    with open(daily_file, "r", encoding="utf-8") as f:
                        daily_entries = json.load(f)
                except Exception:
                    daily_entries = []

            daily_entries.append(sighting_entry)
            daily_entries.sort(key=lambda x: x.get("timestamp", ""))

            daily_temp = tracking_dir / f"{date_str}.json.tmp"
            with open(daily_temp, "w", encoding="utf-8") as f:
                json.dump(daily_entries, f, indent=2, ensure_ascii=False)
            daily_temp.replace(daily_file)

            logger.info(f"Recorded trajectory for [{student_id}] at '{camera_location}' ({camera_id})")
            return sighting_entry

    def get_trajectory(self, student_id: str) -> Optional[dict]:
        """Returns the full chronological trajectory for a student."""
        trajectory_file = self.db_dir / student_id / "tracking" / "trajectory.json"
        if not trajectory_file.exists():
            return None
        with self.lock:
            with open(trajectory_file, "r", encoding="utf-8") as f:
                return json.load(f)

    def get_daily_trajectory(self, student_id: str, date_str: str) -> List[dict]:
        """Returns sightings for a specific date (YYYY-MM-DD)."""
        daily_file = self.db_dir / student_id / "tracking" / f"{date_str}.json"
        if not daily_file.exists():
            return []
        with self.lock:
            with open(daily_file, "r", encoding="utf-8") as f:
                return json.load(f)
