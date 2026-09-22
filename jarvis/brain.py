"""Gemini conversation with automatic tool (function) calling."""
import datetime
import platform
import time

from google.genai import types

from . import ai, config
from .tools import ALL_TOOLS
from .tools.memory import load_facts

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
- For "what do you see" / webcam questions use look_through_camera.
- Use remember when the user tells you something worth keeping (e.g. a friend's email).
- Actions like sending email, posting, or shell commands ask the user to confirm; if they \
decline, accept it gracefully.
If a tool fails, explain briefly and suggest a fix.

Context: {os} computer, screen {screen}. Local time at startup: {now}.
Things you remember about the user:
{facts}"""


def _screen_size():
    try:
        import pyautogui

        w, h = pyautogui.size()
        return f"{w}x{h}"
    except Exception:  # noqa: BLE001
        return "unknown"


class Brain:
    def __init__(self):
        self.chat = None

    def _system_prompt(self):
        facts = load_facts()
        return PERSONA.format(
            title=config.USER_TITLE,
            os=f"{platform.system()} {platform.release()}",
            screen=_screen_size(),
            now=datetime.datetime.now().strftime("%A %d %B %Y %H:%M"),
            facts="\n".join(f"- {f}" for f in facts) or "- nothing yet",
        )

    def _new_chat(self, history=None):
        self.chat = ai.client().chats.create(
            model=ai.model(),
            history=history or [],
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt(),
                tools=ALL_TOOLS,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=25),
                temperature=0.7,
            ),
        )

    def reset(self):
        self.chat = None

    def ask(self, text):
        if self.chat is None:
            self._new_chat()
        tried = set()
        retries = 0
        last_err = None
        while True:
            try:
                resp = self.chat.send_message(text)
                return (resp.text or "").strip() or "Done."
            except Exception as err:  # noqa: BLE001
                last_err = err
                if ai.is_retryable(err) and retries < 1:
                    retries += 1
                    time.sleep(3)
                    continue
                if not (ai.is_model_missing(err) or ai.is_retryable(err)):
                    break
                # Model retired or still overloaded: move to the next one, keeping the conversation.
                tried.add(ai.model())
                remaining = [m for m in ai.candidate_models() if m not in tried]
                if not remaining:
                    break
                ai.set_model(remaining[0])
                self._new_chat(self.chat.get_history())
                retries = 0
        code = getattr(last_err, "code", None)
        if code == 429:
            return f"I've hit the free Gemini rate limit, {config.USER_TITLE}. Give me a minute."
        if code == 503:
            return f"Gemini's servers are overloaded right now, {config.USER_TITLE}. Try again shortly."
        if code in (400, 401, 403):
            return f"Gemini rejected the request: {getattr(last_err, 'message', last_err)}. Please check the API key."
        return f"Something went wrong: {last_err}"
