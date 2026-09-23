import datetime
import json
import threading

from .. import config
from ..bridge import bridge
from ._base import tool

MEMORY_FILE = config.DATA_DIR / "memory.json"


def load_facts():
    try:
        return json.loads(MEMORY_FILE.read_text())
    except (OSError, ValueError):
        return []


@tool
def remember(fact: str) -> str:
    """Permanently remember a fact about the user (name, preferences, contacts' emails, etc.)."""
    facts = load_facts()
    facts.append(fact)
    MEMORY_FILE.write_text(json.dumps(facts, indent=2))
    return "Noted."


@tool
def forget(keyword: str) -> str:
    """Forget every remembered fact that contains the keyword."""
    facts = load_facts()
    kept = [f for f in facts if keyword.lower() not in f.lower()]
    MEMORY_FILE.write_text(json.dumps(kept, indent=2))
    return f"Forgot {len(facts) - len(kept)} fact(s)."


@tool
def get_current_datetime() -> str:
    """Return the current local date and time."""
    return datetime.datetime.now().strftime("%A %d %B %Y, %H:%M")


_speak = None  # set by the app so timers can talk


@tool
def set_timer(minutes: float, message: str = "Your timer is up.") -> str:
    """Set a timer or reminder that Jarvis announces out loud after the given minutes."""

    def fire():
        bridge.log(f"⏰ {message}", "jarvis")
        if _speak:
            _speak(message)

    timer = threading.Timer(minutes * 60, fire)
    timer.daemon = True
    timer.start()
    return f"Timer set for {minutes:g} minute(s)."


@tool
def switch_ai_provider(provider: str) -> str:
    """Switch which AI powers Jarvis from the next request on: 'gemini' or 'chatgpt'."""
    from .. import ai

    return f"Switched to {ai.set_provider(provider)}. The change applies from the next request."


TOOLS = [remember, forget, get_current_datetime, set_timer, switch_ai_provider]
