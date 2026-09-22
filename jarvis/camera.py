"""Webcam access shared by the GUI preview and the vision tools."""
import threading
import time


class Camera:
    def __init__(self, index=0):
        self.index = index
        self.cap = None
        self.frame = None  # latest BGR frame
        self.running = False
        self.lock = threading.Lock()

    def start(self):
        import cv2

        if self.running:
            return True
        self.cap = cv2.VideoCapture(self.index)
        if not self.cap.isOpened():
            self.cap = None
            return False
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _loop(self):
        while self.running and self.cap is not None:
            ok, frame = self.cap.read()
            if ok:
                with self.lock:
                    self.frame = frame
            else:
                time.sleep(0.05)

    def stop(self):
        self.running = False
        time.sleep(0.1)
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        self.frame = None

    def latest_frame(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def snapshot_jpeg(self):
        """JPEG bytes of the current view, opening the camera briefly if needed."""
        import cv2

        frame = self.latest_frame()
        if frame is None:
            cap = cv2.VideoCapture(self.index)
            if not cap.isOpened():
                raise RuntimeError("No camera found.")
            for _ in range(8):  # let auto-exposure settle
                ok, frame = cap.read()
            cap.release()
            if not ok:
                raise RuntimeError("Could not read from the camera.")
        ok, buf = cv2.imencode(".jpg", frame)
        return buf.tobytes()


camera = None


def get_camera():
    global camera
    if camera is None:
        from . import config

        camera = Camera(config.CAMERA_INDEX)
    return camera
