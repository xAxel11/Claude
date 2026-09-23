"""Provider-neutral brain: builds the Jarvis persona and routes each request to the
active AI provider (Gemini or ChatGPT). Switching providers keeps the conversation."""
import datetime
import platform

from . import ai, config
from .bridge import bridge
from .tools import ALL_TOOLS
from .tools.memory import load_facts
from .tools.schema import coerce_args

TOOL_MAP = {f.__name__: f for f in ALL_TOOLS}

PERSONA = """You are J.A.R.V.I.S., the user's personal AI assistant running on their computer, \
modelled on Tony Stark's AI: calm, precise, loyal, quietly witty and very capable. \
Address the user as "{title}".

Your replies are spoken aloud, so:
- Keep them short and natural: usually one to three sentences. Go longer only when asked.
- Never use markdown, bullet symbols, tables or emoji. Write numbers and units so they read well aloud.

You control the user's computer through tools. Use them instead of saying you can't:
- To act in an app or website: open it, then use look_at_screen / find_on_screen to see it, \
then click / type_text / press_keys, and look again to check the result. Work step by step.
- For current information (news, prices, scores, facts after your training) use web_search.
- To show a place use show_map; for directions use get_directions.
- For "what do you see" / webcam questions use look_through_camera; camera_sensors gives live readings (faces, distance, motion, light).
- To write programs: write_code_file (saved in the user's projects folder), then run_code_file to test it, read the output and fix errors until it works. Use open_in_editor to show it.
- The map is yours to drive: show_map, add_map_marker, get_directions (draws the route), where_am_i, clear_map, expand_map.
- Use remember when the user tells you something worth keeping (e.g. a friend's email).
- Actions like sending email, posting, or shell commands ask the user to confirm; if they \
decline, accept it gracefully.
If a tool fails, explain briefly and suggest a fix.

Context: {os} computer, screen {screen}. Local time at startup: {now}. \
You are currently running on {provider}.
Things you remember about the user:
{facts}"""


def _screen_size():
    try:
        import pyautogui

        w, h = pyautogui.size()
        return f"{w}x{h}"
    except Exception:  # noqa: BLE001
        return "unknown"


def execute_tool(name, args):
    fn = TOOL_MAP.get(name)
    if fn is None:
        return f"Unknown tool {name}"
    return fn(**coerce_args(fn, args))


class Brain:
    def __init__(self):
        self.transcript = []   # (role, text) of finished turns, shared by all providers
        self.session = None
        self.session_provider = None

    def _system_prompt(self):
        return PERSONA.format(
            title=config.USER_TITLE,
            os=f"{platform.system()} {platform.release()}",
            screen=_screen_size(),
            now=datetime.datetime.now().strftime("%A %d %B %Y %H:%M"),
            provider=ai.LABELS[ai.provider()],
            facts="\n".join(f"- {f}" for f in load_facts()) or "- nothing yet",
        )

    def reset(self):
        self.transcript = []
        self.session = None

    def ask(self, text):
        name = ai.provider()
        mod = ai.module()
        if self.session is None or self.session_provider != name:
            # new provider: start its session from the text of the conversation so far
            self.session = mod.Session(self._system_prompt(), ALL_TOOLS, execute_tool, self.transcript[-20:])
            self.session_provider = name
        try:
            reply = self.session.ask(text)
        except Exception as err:  # noqa: BLE001
            bridge.log(f"{ai.LABELS[name]} error: {type(err).__name__}: {str(err)[:200]}", "system")
            return mod.friendly_error(err)
        self.transcript += [("user", text), ("assistant", reply)]
        return reply
