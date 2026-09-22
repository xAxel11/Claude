import datetime

from .. import ai, config
from ..bridge import bridge
from ..camera import get_camera
from ._base import tool


@tool
def look_through_camera(question: str = "Describe what you see.") -> str:
    """Look through the webcam and answer a question about what's visible
    (e.g. 'what am I holding?', 'how do I look?')."""
    return ai.vision(question, get_camera().snapshot_jpeg(), mime_type="image/jpeg")


@tool
def take_photo() -> str:
    """Take a photo with the webcam, save it and show it. Returns the file path."""
    path = config.CAPTURES_DIR / f"photo_{datetime.datetime.now():%Y%m%d_%H%M%S}.jpg"
    path.write_bytes(get_camera().snapshot_jpeg())
    bridge.show_image(path)
    return f"Photo saved to {path}"


@tool
def camera_preview(on: bool) -> str:
    """Turn the live webcam preview on the HUD on or off."""
    bridge.set_camera(on)
    return "Camera preview " + ("on." if on else "off.")


TOOLS = [look_through_camera, take_photo, camera_preview]
