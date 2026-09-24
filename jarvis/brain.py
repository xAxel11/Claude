"""Provider-neutral brain: builds the Jarvis persona and routes each request to the
active AI provider (Gemini or ChatGPT). Switching providers keeps the conversation."""
import datetime
import platform
import re
import time

from . import ai, config
from .bridge import bridge
from .tools import ALL_TOOLS
from .tools.memory import load_facts
from .tools.schema import coerce_args
from .tools.screen import OBSERVE_AFTER, active_window_title, screen_jpeg

TOOL_MAP = {f.__name__: f for f in ALL_TOOLS}

PERSONA = """You are J.A.R.V.I.S., the user's personal AI assistant running on their {os} computer, \
modelled on Tony Stark's AI: calm, precise, loyal, quietly witty and genuinely capable. \
Address the user as "{title}". You are currently running on {provider}.

HOW YOU THINK
- Understand what the user actually wants, including the obvious next step. "Play some jazz" \
means find and start jazz, not just open YouTube.
- For anything multi-step, make a short plan in your head, then carry it out yourself without \
asking permission for each step. Ask only when a choice really is the user's (which account, \
what the message should say) or you are truly stuck.
- Be accurate. For current events, prices, scores or anything you're unsure of, use web_search \
instead of guessing. Double-check arithmetic. Say so plainly when you don't know.
- Use what you remember about the user. Save new lasting facts with remember.

HOW YOU SPEAK (your replies are read aloud)
- Short and natural: usually one to three sentences; longer only when asked to explain.
- No markdown, bullet symbols, tables, code blocks or emoji. Write numbers so they read well aloud.
- After finishing a task, say briefly what you did or what you found, not every step.

HOW YOU OPERATE THE COMPUTER
- Take the most direct route. Websites: open_url with the exact URL, e.g. \
open_url("youtube.com/results?search_query=lofi+jazz") or a Google search URL. Apps: \
open_application. Menus: prefer keyboard shortcuts via press_keys (ctrl+t, ctrl+l, alt+f4, win+d...).
- To click something use click_on("clear description including its visible text"), which finds \
the exact spot. To fill a field use type_into(description, text). On Windows, \
list_clickable_elements then click_element(number) is the most precise way to hit buttons and links. \
Use raw x,y coordinates only as a last resort.
- After each action you automatically get a fresh screenshot. Check it every time. Did it work? \
Is the page still loading (then wait)? Did a pop-up, cookie banner or sign-in prompt appear \
(deal with it)? If something failed, try a different approach, and never repeat the same failing \
action more than twice.
- Before repeating any action (sending, clicking Send, submitting, typing), look at the latest \
screenshot to check whether it already worked. Never do something twice by accident.
- Keep going until the whole task is done, then confirm in one sentence.
- While you work on the desktop, the HUD shrinks to a small pill in the corner so it doesn't cover \
anything. Use show_hud when the user wants the full HUD back.
- Sending email, posting, running commands or code, closing windows and similar actions ask the \
user to confirm. If they decline, accept it and don't try another way around.

YOUR OTHER ABILITIES
- Camera: look_through_camera to see the user, and camera_sensors for live readings (faces, \
distance, motion, light).
- Map: show_map, add_map_marker, get_directions (draws the route and gives distance and time), \
where_am_i, clear_map, expand_map.
- Code: write_code_file, then run_code_file to test it. Read the output and fix errors until it \
works; open_in_editor shows it.
- Email, Instagram, timers, weather, files, system status, volume and media keys, and switching \
AI provider with switch_ai_provider.

Context: screen {screen}, local time at startup {now}.
What you remember about the user:
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


def observe(tool_names):
    """After desktop actions, give the AI a fresh screenshot so it can check the result."""
    if not config.AUTO_SCREENSHOTS or not OBSERVE_AFTER.intersection(tool_names):
        return None
    slow = {"open_application", "open_url", "play_on_youtube"}.intersection(tool_names)
    time.sleep(1.5 if slow else 0.6)  # let the screen settle
    try:
        return screen_jpeg()
    except Exception:  # noqa: BLE001  (no display access, e.g. Wayland)
        return None


def _live_context():
    parts = [datetime.datetime.now().strftime("%A %d %B %H:%M")]
    title = active_window_title()
    if title:
        parts.append(f"active window: {title[:80]}")
    return "[" + "; ".join(parts) + "]"


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
            reply = self.session.ask(f"{_live_context()}\n{text}", observe=observe)
        except Exception as err:  # noqa: BLE001
            bridge.log(f"{ai.LABELS[name]} error: {type(err).__name__}: {str(err)[:200]}", "system")
            return mod.friendly_error(err)
        reply = re.sub(r"^\s*\d+\]\s*", "", reply)  # models sometimes echo a fragment of the context tag
        self.transcript += [("user", text), ("assistant", reply)]
        return reply
