"""Webcam shared by the HUD preview and the vision tools, with live "sensors":
face tracking with estimated distance, motion detection and light level."""
import threading
import time

FACE_WIDTH_CM = 15.0  # average adult face width, for the distance estimate


def _load_face_detector(cv2):
    """Haar face detector (OpenCV 4.x). Returns None if unavailable; motion/light still work."""
    try:
        return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except AttributeError:
        print("[camera] this OpenCV has no face detector; run: pip install \"opencv-python<5\"")
        return None


class Camera:
    def __init__(self, index=0):
        self.index = index
        self.cap = None
        self.frame = None  # latest BGR frame
        self.running = False
        self.lock = threading.Lock()
        self.sensors = {}
        self._face_detector = None
        self._prev_gray = None

    # ---------------------------------------------------------------- capture
    def start(self):
        import cv2

        if self.running:
            return True
        self.cap = cv2.VideoCapture(self.index)
        if not self.cap.isOpened():
            self.cap = None
            return False
        self._face_detector = _load_face_detector(cv2)
        self.sensors = {"frames": 0, "faces": [], "motion": 0.0, "light": 0.0, "present_since": None,
                        "last_seen": None, "fps": 0.0}
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _loop(self):
        last_analysis = 0.0
        frames, fps_t = 0, time.time()
        while self.running and self.cap is not None:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            with self.lock:
                self.frame = frame
            frames += 1
            now = time.time()
            if now - fps_t >= 1:
                self.sensors["fps"] = frames / (now - fps_t)
                frames, fps_t = 0, now
            if now - last_analysis >= 0.15:  # ~7 sensor updates per second
                last_analysis = now
                self._analyse(frame, now)

    def _analyse(self, frame, now):
        import cv2

        h, w = frame.shape[:2]
        scale = 320 / w
        small = cv2.resize(frame, (320, int(h * scale)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (9, 9), 0)

        motion = 0.0
        if self._prev_gray is not None and self._prev_gray.shape == blur.shape:
            diff = cv2.absdiff(self._prev_gray, blur)
            motion = float((diff > 25).mean() * 100)
        self._prev_gray = blur

        found = []
        if self._face_detector is not None:
            found = self._face_detector.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(28, 28))
        faces = []
        for (x, y, fw, fh) in found:
            x, y, fw, fh = (int(v / scale) for v in (x, y, fw, fh))
            # pinhole estimate: focal length ≈ frame width for a typical ~60° webcam
            distance_m = FACE_WIDTH_CM * w / max(fw, 1) / 100
            faces.append({"box": (x, y, fw, fh), "distance_m": distance_m})
        faces.sort(key=lambda f: -f["box"][2])

        s = self.sensors
        s["frames"] = s.get("frames", 0) + 1
        s["faces"] = faces
        s["motion"] = motion
        s["light"] = float(gray.mean() / 255 * 100)
        s["size"] = (w, h)
        if faces:
            s["last_seen"] = now
            if s.get("present_since") is None:
                s["present_since"] = now
        elif s.get("last_seen") and now - s["last_seen"] > 3:
            s["present_since"] = None

    def stop(self):
        self.running = False
        time.sleep(0.1)
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        self.frame = None
        self._prev_gray = None

    def latest_frame(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    # ---------------------------------------------------------------- readings
    def sensor_report(self):
        s = self.sensors
        faces = s.get("faces", [])
        w = s.get("size", (1, 1))[0]
        parts = [f"{len(faces)} face(s) detected"]
        for i, f in enumerate(faces[:3], 1):
            x, _, fw, _ = f["box"]
            cx = (x + fw / 2) / w
            where = "left" if cx < 0.38 else "right" if cx > 0.62 else "centre"
            # the preview is mirrored, so camera-left is the user's right
            parts.append(f"subject {i}: {where} of frame, about {f['distance_m']:.1f} m away")
        motion = s.get("motion", 0)
        parts.append(f"motion {motion:.0f}% ({'still' if motion < 2 else 'some movement' if motion < 10 else 'lots of movement'})")
        light = s.get("light", 0)
        parts.append(f"light level {light:.0f}% ({'dark' if light < 25 else 'dim' if light < 45 else 'well lit'})")
        if s.get("present_since"):
            parts.append(f"someone present for {time.time() - s['present_since']:.0f} s")
        return ", ".join(parts)

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


def draw_hud(frame, sensors, tick):
    """Draw the Iron-Man style sensor overlay onto a BGR frame (mirrored preview)."""
    import cv2

    frame = cv2.flip(frame, 1)
    h, w = frame.shape[:2]
    cyan, amber, green = (255, 230, 62), (71, 179, 255), (158, 255, 60)
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = w / 640  # text scale: the preview is shown small, so draw big
    # scan line
    y = int((tick * 6) % h)
    cv2.line(frame, (0, y), (w, y), cyan, 1)
    # frame corners
    L = max(w // 12, 20)
    for (cx, cy, dx, dy) in ((0, 0, 1, 1), (w - 1, 0, -1, 1), (0, h - 1, 1, -1), (w - 1, h - 1, -1, -1)):
        cv2.line(frame, (cx, cy), (cx + dx * L, cy), cyan, 3)
        cv2.line(frame, (cx, cy), (cx, cy + dy * L), cyan, 3)

    for i, f in enumerate(sensors.get("faces", [])):
        x, y0, fw, fh = f["box"]
        x = w - x - fw  # mirror
        col = green if i == 0 else amber
        l = fw // 4
        for (px, py, dx, dy) in ((x, y0, 1, 1), (x + fw, y0, -1, 1), (x, y0 + fh, 1, -1), (x + fw, y0 + fh, -1, -1)):
            cv2.line(frame, (px, py), (px + dx * l, py), col, 3)
            cv2.line(frame, (px, py), (px, py + dy * l), col, 3)
        cx, cy = x + fw // 2, y0 + fh // 2
        r = int(fw * 0.12 + 3 * (1 + (tick % 20) / 20))
        cv2.circle(frame, (cx, cy), r, col, 1)
        cv2.line(frame, (cx - r - 6, cy), (cx + r + 6, cy), col, 1)
        cv2.line(frame, (cx, cy - r - 6), (cx, cy + r + 6), col, 1)
        label = f"{'TARGET LOCKED' if i == 0 else f'SUBJECT {i + 1}'}  {f['distance_m']:.1f}m"
        ly = y0 - 10 if y0 > 50 * fs else y0 + fh + int(28 * fs)  # below the box near the top edge
        cv2.putText(frame, label, (x, ly), font, 0.8 * fs, col, 2, cv2.LINE_AA)

    motion, light = sensors.get("motion", 0), sensors.get("light", 0)
    lines = [
        f"FACES {len(sensors.get('faces', []))}",
        f"MOTION {motion:4.0f}%",
        f"LIGHT {light:4.0f}%",
        f"FPS {sensors.get('fps', 0):4.0f}",
    ]
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (12, int((34 + i * 30) * fs)), font, 0.8 * fs, cyan, 2, cv2.LINE_AA)
    # motion bar
    bar = int(min(motion, 30) / 30 * (w // 3))
    cv2.rectangle(frame, (w - w // 3 - 12, 14), (w - 12, 28), cyan, 2)
    cv2.rectangle(frame, (w - w // 3 - 12, 14), (w - w // 3 - 12 + bar, 28), amber if motion > 10 else cyan, -1)
    return frame


camera = None


def get_camera():
    global camera
    if camera is None:
        from . import config

        camera = Camera(config.CAMERA_INDEX)
    return camera
