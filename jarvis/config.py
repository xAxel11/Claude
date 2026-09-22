import os
import platform
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = ROOT_DIR / "jarvis_data"
DATA_DIR.mkdir(exist_ok=True)
CAPTURES_DIR = DATA_DIR / "captures"
CAPTURES_DIR.mkdir(exist_ok=True)


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int_or_none(name):
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else None


OS_NAME = platform.system()  # "Windows", "Linux", "Darwin"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
# Tried in order if the model above is retired, overloaded or rate-limited.
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]

VOICE = os.getenv("JARVIS_VOICE", "en-GB-RyanNeural")
VOICE_RATE = os.getenv("JARVIS_VOICE_RATE", "+5%")
VOICE_PITCH = os.getenv("JARVIS_VOICE_PITCH", "-2Hz")

USER_TITLE = os.getenv("USER_TITLE", "sir")
LANGUAGE = os.getenv("LANGUAGE", "en-US")
WAKE_WORD = os.getenv("WAKE_WORD", "jarvis").lower()
MIC_INDEX = _int_or_none("MIC_INDEX")
CAMERA_INDEX = _int_or_none("CAMERA_INDEX") or 0

CONFIRM_ACTIONS = _bool("CONFIRM_ACTIONS", True)
START_FULLSCREEN = _bool("START_FULLSCREEN", True)

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
