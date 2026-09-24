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

# Which AI answers by default: "gemini" or "openai" (switchable in the HUD)
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").strip().lower()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "auto").strip()
# "auto" picks the best model your key can use and switches when one is busy.
# Set a model name (e.g. gemini-3-flash-preview) to try that one first.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "auto").strip()
# Seconds to wait for a model before switching to another one.
REQUEST_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "40"))
# If a model is silent this long, a backup model is asked too and the fastest answer wins.
HEDGE_SECONDS = float(os.getenv("GEMINI_HEDGE_SECONDS", "8"))

VOICE = os.getenv("JARVIS_VOICE", "en-GB-RyanNeural")
VOICE_RATE = os.getenv("JARVIS_VOICE_RATE", "+5%")
VOICE_PITCH = os.getenv("JARVIS_VOICE_PITCH", "-2Hz")

USER_TITLE = os.getenv("USER_TITLE", "sir")
LANGUAGE = os.getenv("LANGUAGE", "en-US")
WAKE_WORD = os.getenv("WAKE_WORD", "jarvis").lower()
MIC_INDEX = _int_or_none("MIC_INDEX")
CAMERA_INDEX = _int_or_none("CAMERA_INDEX") or 0

CONFIRM_ACTIONS = _bool("CONFIRM_ACTIONS", True)
# Show the AI a screenshot after each desktop action so it can check its work
AUTO_SCREENSHOTS = _bool("AUTO_SCREENSHOTS", True)
START_FULLSCREEN = _bool("START_FULLSCREEN", True)

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")

# Where Jarvis saves code it writes
CODE_DIR = Path(os.path.expanduser(os.getenv("CODE_DIR", "~/JarvisProjects")))
# Greet you when the camera sees you come back
PRESENCE_GREETING = _bool("PRESENCE_GREETING", True)

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
