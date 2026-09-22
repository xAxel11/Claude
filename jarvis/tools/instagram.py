"""Instagram via the unofficial instagrapi library.

Automating Instagram is against its terms of service and can get an account
rate-limited or flagged, so every post and DM needs the user's approval.
"""
import os
from pathlib import Path

from .. import config
from ..bridge import bridge
from ._base import tool

SESSION_FILE = config.DATA_DIR / "instagram.session.json"
_client = None


def _ig():
    global _client
    if _client is not None:
        return _client
    if not (config.INSTAGRAM_USERNAME and config.INSTAGRAM_PASSWORD):
        raise RuntimeError("Instagram isn't set up. Add INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD to .env.")
    from instagrapi import Client

    cl = Client()
    cl.delay_range = [1, 3]
    if SESSION_FILE.exists():
        cl.load_settings(SESSION_FILE)
    cl.login(config.INSTAGRAM_USERNAME, config.INSTAGRAM_PASSWORD)
    cl.dump_settings(SESSION_FILE)
    _client = cl
    return cl


def _as_jpeg(path):
    from PIL import Image

    p = Path(os.path.expanduser(path))
    if p.suffix.lower() in (".jpg", ".jpeg"):
        return p
    out = p.with_suffix(".jpg")
    Image.open(p).convert("RGB").save(out, quality=95)
    return out


@tool
def instagram_login() -> str:
    """Log in to the user's Instagram account (uses credentials from .env)."""
    cl = _ig()
    return f"Logged in to Instagram as {cl.username}."


@tool
def instagram_post_photo(image_path: str, caption: str) -> str:
    """Post a photo to the user's Instagram feed. image_path can be a screenshot or camera
    photo path returned by other tools. The user must approve the post first."""
    bridge.show_image(image_path)
    if not bridge.confirm(f"Post this to Instagram?\n\nImage: {image_path}\nCaption: {caption}"):
        return "The user cancelled the post."
    media = _ig().photo_upload(_as_jpeg(image_path), caption)
    return f"Posted to Instagram: https://www.instagram.com/p/{media.code}/"


@tool
def instagram_send_dm(username: str, message: str) -> str:
    """Send an Instagram direct message to a user. The user must approve it first."""
    if not bridge.confirm(f"Send this Instagram DM to @{username}?\n\n{message}"):
        return "The user cancelled the message."
    cl = _ig()
    cl.direct_send(message, [cl.user_id_from_username(username)])
    return f"Sent DM to @{username}."


@tool
def instagram_profile(username: str) -> str:
    """Look up an Instagram profile: bio, follower and post counts."""
    u = _ig().user_info_by_username(username)
    return (
        f"@{u.username} ({u.full_name}): {u.follower_count} followers, "
        f"{u.following_count} following, {u.media_count} posts. Bio: {u.biography}"
    )


TOOLS = [instagram_login, instagram_post_photo, instagram_send_dm, instagram_profile]
