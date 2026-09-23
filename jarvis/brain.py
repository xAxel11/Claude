"""Gemini conversation with tool (function) calling.

The tool loop runs here rather than inside the SDK so that each tool runs exactly
once: only the model calls are retried or switched to another model (see ai.run).
"""
import datetime
import inspect
import platform

from google.genai import types

from . import ai, config
from .tools import ALL_TOOLS
from .tools.memory import load_facts

MAX_STEPS = 25          # tool round-trips per request
MAX_HISTORY = 60        # contents kept in the conversation

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

TOOL_MAP = {f.__name__: f for f in ALL_TOOLS}


def _screen_size():
    try:
        import pyautogui

        w, h = pyautogui.size()
        return f"{w}x{h}"
    except Exception:  # noqa: BLE001
        return "unknown"


def _coerce_args(fn, args):
    """JSON numbers arrive as floats; convert them to the int/bool the tool expects."""
    params = inspect.signature(fn).parameters
    out = {}
    for key, value in (args or {}).items():
        if key not in params:
            continue
        ann = params[key].annotation
        if ann is int and isinstance(value, float):
            value = int(value)
        elif ann is bool and isinstance(value, str):
            value = value.lower() in ("true", "1", "yes")
        out[key] = value
    return out


def _strip_signatures(contents):
    """Gemma models reject Gemini's thought signatures / thought parts."""
    clean = []
    for c in contents:
        parts = [
            types.Part(text=p.text, function_call=p.function_call, function_response=p.function_response)
            for p in c.parts or []
            if not p.thought
        ]
        if parts:
            clean.append(types.Content(role=c.role, parts=parts))
    return clean


class Brain:
    def __init__(self):
        self.history = []
        self._config = None

    def _system_prompt(self):
        facts = load_facts()
        return PERSONA.format(
            title=config.USER_TITLE,
            os=f"{platform.system()} {platform.release()}",
            screen=_screen_size(),
            now=datetime.datetime.now().strftime("%A %d %B %Y %H:%M"),
            facts="\n".join(f"- {f}" for f in facts) or "- nothing yet",
        )

    def _build_config(self):
        api = ai.client()._api_client
        declarations = [types.FunctionDeclaration.from_callable(client=api, callable=f) for f in ALL_TOOLS]
        return types.GenerateContentConfig(
            system_instruction=self._system_prompt(),
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.7,
        )

    def reset(self):
        self.history = []
        self._config = None

    def _generate(self, model_name):
        contents = _strip_signatures(self.history) if "gemma" in model_name else self.history
        return ai.client().models.generate_content(model=model_name, contents=contents, config=self._config)

    def _trim_history(self):
        if len(self.history) <= MAX_HISTORY:
            return
        # cut at a plain user message so no tool call loses its response
        for i in range(len(self.history) - MAX_HISTORY // 2, len(self.history)):
            c = self.history[i]
            if c.role == "user" and c.parts and c.parts[0].text:
                self.history = self.history[i:]
                return

    def ask(self, text):
        if self._config is None:
            self._config = self._build_config()
        self._trim_history()
        start = len(self.history)
        self.history.append(types.Content(role="user", parts=[types.Part(text=text)]))
        try:
            for _ in range(MAX_STEPS):
                resp = ai.run(self._generate)
                content = resp.candidates[0].content if resp.candidates else None
                if content is None or not content.parts:
                    self.history.append(types.Content(role="model", parts=[types.Part(text="(no answer)")]))
                    return f"I'm afraid I can't help with that one, {config.USER_TITLE}."
                self.history.append(content)
                calls = [p.function_call for p in content.parts if p.function_call]
                if not calls:
                    reply = "".join(p.text for p in content.parts if p.text and not p.thought).strip()
                    return reply or "Done."
                results = []
                for call in calls:
                    fn = TOOL_MAP.get(call.name)
                    result = fn(**_coerce_args(fn, call.args)) if fn else f"Unknown tool {call.name}"
                    results.append(types.Part.from_function_response(name=call.name, response={"result": result}))
                self.history.append(types.Content(role="user", parts=results))
            return f"That took more steps than I'm allowed, {config.USER_TITLE}. I've stopped here."
        except Exception as err:  # noqa: BLE001
            # keep the conversation valid: drop a half-finished turn
            del self.history[start:]
            if ai.is_fatal(err):
                return f"Gemini rejected the API key: {getattr(err, 'message', err)}. Please check GEMINI_API_KEY in .env."
            if ai.error_code(err) == 429:
                return f"Every free Gemini model is rate-limited right now, {config.USER_TITLE}. Give me a minute."
            return f"All Gemini models are busy right now, {config.USER_TITLE}. Please try again in a moment."
