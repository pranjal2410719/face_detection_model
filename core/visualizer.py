"""
High-Performance HUD Visualizer for Real-Time Face Recognition.
Draws sleek bounding boxes, fixed student ID tags, HUD statistics, and event alerts.
"""
import time
import cv2
import numpy as np
from typing import List, Dict, Optional

# Color Palette (BGR)
COLOR_VERIFIED = (80, 220, 80)       # Bright Green for verified student
COLOR_UNCERTAIN = (0, 200, 255)      # Amber / Yellow for ambiguous/uncertain face
COLOR_UNKNOWN = (60, 60, 220)        # Soft Red for unknown person
COLOR_HUD_BG = (20, 20, 24)          # Dark Slate HUD background
COLOR_TEXT_WHITE = (255, 255, 255)   # White
COLOR_ACCENT_BLUE = (255, 180, 50)   # Cyan / Light Blue
COLOR_ALERT = (50, 220, 255)         # Bright Yellow/Amber Alert

class Visualizer:
    """
    Renders bounding boxes with fixed Student ID tags and real-time HUD overlays.
    """
    def __init__(self, camera_id: str = "CAM-01", show_landmarks: bool = False):
        self.camera_id = camera_id
        self.show_landmarks = show_landmarks

    def draw_corner_rect(self, img: np.ndarray, pt1: tuple, pt2: tuple, color: tuple, 
                         thickness: int = 2, corner_len: int = 15):
        """Draws modern corner brackets around the bounding box."""
        x1, y1 = pt1
        x2, y2 = pt2

        # Base light rectangle
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)

        # Top-left corner
        cv2.line(img, (x1, y1), (x1 + corner_len, y1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1, y1), (x1, y1 + corner_len), color, thickness, cv2.LINE_AA)

        # Top-right corner
        cv2.line(img, (x2, y1), (x2 - corner_len, y1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x2, y1), (x2, y1 + corner_len), color, thickness, cv2.LINE_AA)

        # Bottom-left corner
        cv2.line(img, (x1, y2), (x1 + corner_len, y2), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1, y2), (x1, y2 - corner_len), color, thickness, cv2.LINE_AA)

        # Bottom-right corner
        cv2.line(img, (x2, y2), (x2 - corner_len, y2), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x2, y2), (x2, y2 - corner_len), color, thickness, cv2.LINE_AA)

    def draw_tag(self, img: np.ndarray, text: str, pt: tuple, bg_color: tuple, 
                 text_color: tuple = COLOR_TEXT_WHITE, font_scale: float = 0.55, thickness: int = 1):
        """Draws a solid filled tag pill for student ID display."""
        x, y = pt
        font = cv2.FONT_HERSHEY_DUPLEX
        (tw, th), baseline = cv2.getTextSize(text, font, font_scale, thickness)
        
        pad_x, pad_y = 6, 4
        box_y1 = max(0, y - th - (pad_y * 2))
        box_y2 = y
        box_x1 = max(0, x)
        box_x2 = min(img.shape[1] - 1, x + tw + (pad_x * 2))

        # Filled tag pill
        cv2.rectangle(img, (box_x1, box_y1), (box_x2, box_y2), bg_color, -1)
        # Tag border
        cv2.rectangle(img, (box_x1, box_y1), (box_x2, box_y2), (255, 255, 255), 1, cv2.LINE_AA)
        # Text
        cv2.putText(img, text, (box_x1 + pad_x, box_y2 - pad_y), font, font_scale, text_color, thickness, cv2.LINE_AA)

    def render(self, frame: np.ndarray, recognitions: List[Dict], 
               fps: float, latency_ms: float, enrolled_count: int,
               last_event_student: Optional[str] = None, last_event_time: float = 0.0) -> np.ndarray:
        """
        Renders bounding boxes, fixed Student ID tags, and HUD information.
        """
        canvas = frame.copy()
        now = time.time()

        # 1. Render Face Detections & Tags
        for rec in recognitions:
            x, y, w, h = rec["bbox"]
            student_id = rec["student_id"]
            status = rec.get("status", "VERIFIED" if student_id != "UNKNOWN" else "UNKNOWN")
            confidence = rec["confidence"]
            student_info = rec.get("metadata")

            if status == "VERIFIED":
                color = COLOR_VERIFIED
                name = student_info.get("name") if student_info else None
                if name and name != student_id:
                    tag_text = f"ID: {student_id} ({name}) | {int(confidence * 100)}%"
                else:
                    tag_text = f"ID: {student_id} | {int(confidence * 100)}%"
                tag_bg = (30, 130, 30)
            elif status == "UNCERTAIN":
                color = COLOR_UNCERTAIN
                tag_text = f"UNCERTAIN: {student_id}? | {int(confidence * 100)}%"
                tag_bg = (20, 120, 180) # Amber tag
            else:
                color = COLOR_UNKNOWN
                tag_text = f"ID: UNKNOWN | {int(confidence * 100)}%"
                tag_bg = (40, 40, 160)

            # Corner bounding box
            self.draw_corner_rect(canvas, (x, y), (x + w, y + h), color, thickness=2, corner_len=min(20, w // 4))

            # Draw tag directly above bounding box (or slightly inside if near top boundary)
            tag_y = y - 4 if y > 30 else y + 25
            self.draw_tag(canvas, tag_text, (x, tag_y), bg_color=tag_bg)

            # Optional landmarks
            if self.show_landmarks and "landmarks" in rec:
                for lm_x, lm_y in rec["landmarks"]:
                    cv2.circle(canvas, (lm_x, lm_y), 2, (0, 255, 255), -1, cv2.LINE_AA)

        # 2. Render Top HUD Bar
        hud_height = 36
        # Translucent top bar
        overlay = canvas.copy()
        cv2.rectangle(overlay, (0, 0), (canvas.shape[1], hud_height), COLOR_HUD_BG, -1)
        cv2.addWeighted(overlay, 0.75, canvas, 0.25, 0, canvas)
        cv2.line(canvas, (0, hud_height), (canvas.shape[1], hud_height), (60, 60, 70), 1)

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.45

        # FPS & Latency
        fps_text = f"FPS: {fps:.1f}  |  Latency: {latency_ms:.1f}ms"
        cv2.putText(canvas, fps_text, (12, 23), font, font_scale, COLOR_TEXT_WHITE, 1, cv2.LINE_AA)

        # Camera ID & Enrolled DB count
        center_text = f"[{self.camera_id}]  Enrolled: {enrolled_count}"
        cv2.putText(canvas, center_text, (canvas.shape[1] // 2 - 90, 23), font, font_scale, COLOR_ACCENT_BLUE, 1, cv2.LINE_AA)

        # Clock
        time_str = time.strftime("%H:%M:%S")
        cv2.putText(canvas, time_str, (canvas.shape[1] - 80, 23), font, font_scale, COLOR_TEXT_WHITE, 1, cv2.LINE_AA)

        # 3. Dispatched Event Notification Banner
        if last_event_student and (now - last_event_time < 2.5):
            alert_text = f">> EVENT DISPATCHED: STUDENT [{last_event_student}] <<"
            alert_bar_y = canvas.shape[0] - 28
            # Semi-transparent alert pill
            cv2.rectangle(canvas, (0, alert_bar_y - 4), (canvas.shape[1], canvas.shape[0]), (0, 100, 0), -1)
            cv2.putText(canvas, alert_text, (canvas.shape[1] // 2 - 180, canvas.shape[0] - 8),
                        cv2.FONT_HERSHEY_DUPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

        return canvas
