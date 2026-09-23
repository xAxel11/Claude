import datetime
import time

from .. import ai, config
from ..bridge import bridge
from ..camera import get_camera
from ._base import tool


def _ensure_camera():
    cam = get_camera()
    if not cam.running:
        bridge.set_camera(True)
        for _ in range(40):  # wait up to 4 s for the sensors to warm up
            time.sleep(0.1)
            if cam.sensors.get("frames", 0) > 10:
                break
    return cam


@tool
def camera_sensors() -> str:
    """Read Jarvis's live camera sensors: faces detected, where they are, estimated
    distance, motion level, light level and how long someone has been present."""
    cam = _ensure_camera()
    return cam.sensor_report() if cam.running else "The camera isn't available."


@tool
def look_through_camera(question: str = "Describe what you see.") -> str:
    """Look through the webcam and answer a question about what's visible
    (e.g. 'what am I holding?', 'how do I look?', 'what am I wearing?')."""
    cam = get_camera()
    readings = cam.sensor_report() if cam.running else ""
    prompt = question + (f"\n(Live sensor readings: {readings})" if readings else "")
    return ai.vision(prompt, cam.snapshot_jpeg(), mime_type="image/jpeg")


@tool
def take_photo() -> str:
    """Take a photo with the webcam, save it and show it. Returns the file path."""
    path = config.CAPTURES_DIR / f"photo_{datetime.datetime.now():%Y%m%d_%H%M%S}.jpg"
    path.write_bytes(get_camera().snapshot_jpeg())
    bridge.show_image(path)
    return f"Photo saved to {path}"


@tool
def camera_preview(on: bool) -> str:
    """Turn the live webcam feed and sensors on the HUD on or off."""
    bridge.set_camera(on)
    return "Camera and sensors " + ("online." if on else "offline.")


TOOLS = [camera_sensors, look_through_camera, take_photo, camera_preview]
