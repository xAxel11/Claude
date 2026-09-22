"""Thread-safe link between the worker thread (brain + tools) and the Tk GUI.

Tk must only be touched from the main thread, so tools post messages here and
the GUI drains the queue on a timer.
"""
import queue
import threading

from . import config


class UIBridge:
    def __init__(self):
        self.queue = queue.Queue()

    def post(self, kind, *args):
        self.queue.put((kind, args))

    def log(self, text, who="system"):
        self.post("log", who, text)

    def set_state(self, state):
        """idle | listening | thinking | speaking"""
        self.post("state", state)

    def show_map(self, lat, lon, label, zoom=12, map_type="road"):
        self.post("map", lat, lon, label, zoom, map_type)

    def show_image(self, path):
        self.post("image", str(path))

    def set_camera(self, on):
        self.post("camera", on)

    def set_clipboard(self, text, timeout=5):
        done = threading.Event()
        self.post("clipboard", text, done)
        done.wait(timeout)

    def confirm(self, message, timeout=120):
        """Block the calling (worker) thread until the user clicks Yes/No."""
        if not config.CONFIRM_ACTIONS:
            return True
        done = threading.Event()
        result = {"ok": False}
        self.post("confirm", message, done, result)
        done.wait(timeout)
        return result["ok"]


bridge = UIBridge()
