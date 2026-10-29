"""
Threaded Camera Capture Stream for Zero-Lag Real-Time Processing.
"""
import time
import threading
import cv2
import logging

logger = logging.getLogger("CameraStream")

class CameraStream:
    """
    High-performance threaded camera reader.
    Continuously drains camera buffer to ensure zero buffer latency (no lag)
    and delivers the freshest frame at max hardware frame rate.
    """
    def __init__(self, src=0, width=640, height=480, target_fps=30):
        self.src = src
        self.width = width
        self.height = height
        self.target_fps = target_fps
        
        self.cap = cv2.VideoCapture(self.src)
        if not self.cap.isOpened():
            raise RuntimeError(f"Unable to open camera source: {self.src}")
            
        # Configure camera properties
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
        # Attempt to set MJPG codec for higher FPS on USB webcams
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        
        # Read actual parameters granted by driver
        self.actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        logger.info(f"Camera opened: {self.actual_width}x{self.actual_height} @ ~{self.actual_fps} FPS")

        # Initial frame
        ret, frame = self.cap.read()
        if not ret or frame is None:
            raise RuntimeError(f"Failed to read initial frame from camera source: {self.src}")
            
        self.latest_frame = frame
        self.latest_timestamp = time.time()
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        
        # FPS estimation
        self._fps_count = 0
        self._fps_start_time = time.time()
        self.current_fps = 0.0

    def start(self):
        """Starts the capture thread."""
        if self.running:
            return self
        self.running = True
        self.thread = threading.Thread(target=self._update_loop, daemon=True, name="CameraWorker")
        self.thread.start()
        return self

    def _update_loop(self):
        while self.running:
            # Grab grabs frame without full decoding for maximum throughput
            grabbed = self.cap.grab()
            if not grabbed:
                time.sleep(0.005)
                continue
                
            ret, frame = self.cap.retrieve()
            if not ret or frame is None:
                continue
                
            now = time.time()
            with self.lock:
                self.latest_frame = frame
                self.latest_timestamp = now
                self._fps_count += 1
                elapsed = now - self._fps_start_time
                if elapsed >= 1.0:
                    self.current_fps = self._fps_count / elapsed
                    self._fps_count = 0
                    self._fps_start_time = now

    def read(self):
        """Returns (ret, latest_frame, timestamp)."""
        with self.lock:
            return True, self.latest_frame.copy(), self.latest_timestamp

    def get_fps(self):
        """Returns measured capture FPS."""
        return self.current_fps

    def stop(self):
        """Stops thread and releases camera."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.cap and self.cap.isOpened():
            self.cap.release()
        logger.info("Camera stream stopped.")
