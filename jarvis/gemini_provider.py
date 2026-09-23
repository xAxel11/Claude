"""Google Gemini (free tier)."""
import re

from google import genai
from google.genai import errors, types

from . import config
from .model_pool import ModelPool

LABEL = "Gemini"
PREFERRED = [
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
]
_SKIP = re.compile(r"tts|image|audio|live|transcribe|robotics|computer-use|embedding|lyria|banana|research|antigravity")
_client = None


def available():
    return bool(config.GEMINI_API_KEY) and not config.GEMINI_API_KEY.startswith("paste-")


def client():
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=config.REQUEST_TIMEOUT * 1000),
        )
    return _client


def _fetch_models():
    return [
        m.name.split("/")[-1]
        for m in client().models.list()
        if "generateContent" in (m.supported_actions or [])
    ]


def error_code(err):
    return getattr(err, "code", None) if isinstance(err, errors.APIError) else None


def is_fatal(err):
    """Errors that switching models can't fix (bad or blocked API key)."""
    if not isinstance(err, errors.APIError):
        return False
    msg = str(err).lower()
    return err.code in (401, 403) or "api key" in msg or "api_key" in msg


def friendly_error(err):
    if is_fatal(err):
        return f"Gemini rejected the API key: {getattr(err, 'message', err)}. Check GEMINI_API_KEY in .env."
    if error_code(err) == 429:
        return f"Every free Gemini model is rate-limited right now, {config.USER_TITLE}. Give me a minute, or switch to ChatGPT."
    return f"All Gemini models are busy right now, {config.USER_TITLE}. Try again in a moment, or switch to ChatGPT."


pool = ModelPool(
    LABEL,
    _fetch_models,
    PREFERRED,
    wanted=lambda: "" if config.GEMINI_MODEL in ("", "auto") else config.GEMINI_MODEL,
    keep=lambda n: ("flash" in n or "gemma" in n) and not _SKIP.search(n),
    error_code=error_code,
    is_fatal=is_fatal,
)


def vision(prompt, image_bytes, mime_type="image/png", json_mode=False):
    cfg = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    contents = [types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt]
    resp = pool.run(lambda m: client().models.generate_content(model=m, contents=contents, config=cfg))
    return resp.text or ""


def web_search(query):
    """Google Search grounding (small free quota). Returns None if unavailable."""
    try:
        cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        resp = client().models.generate_content(
            model=pool.current(), contents=f"Search the web and answer concisely with key facts: {query}", config=cfg
        )
        return resp.text or None
    except Exception:  # noqa: BLE001
        return None


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


class Session:
    """One conversation with tool calling. Tools run here, exactly once, even if
    the model is switched mid-request (only the model calls are retried)."""

    def __init__(self, system_prompt, tools, execute, seed=()):
        api = client()._api_client
        declarations = [types.FunctionDeclaration.from_callable(client=api, callable=f) for f in tools]
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.7,
        )
        self.execute = execute
        self.history = [
            types.Content(role="user" if role == "user" else "model", parts=[types.Part(text=text)])
            for role, text in seed
        ]

    def _generate(self, model_name):
        contents = _strip_signatures(self.history) if "gemma" in model_name else self.history
        return client().models.generate_content(model=model_name, contents=contents, config=self.config)

    def _trim(self, max_items=60):
        if len(self.history) <= max_items:
            return
        for i in range(len(self.history) - max_items // 2, len(self.history)):
            c = self.history[i]
            if c.role == "user" and c.parts and c.parts[0].text:
                self.history = self.history[i:]
                return

    def ask(self, text, max_steps=25):
        self._trim()
        start = len(self.history)
        self.history.append(types.Content(role="user", parts=[types.Part(text=text)]))
        try:
            for _ in range(max_steps):
                resp = pool.run(self._generate)
                content = resp.candidates[0].content if resp.candidates else None
                if content is None or not content.parts:
                    self.history.append(types.Content(role="model", parts=[types.Part(text="(no answer)")]))
                    return f"I'm afraid I can't help with that one, {config.USER_TITLE}."
                self.history.append(content)
                calls = [p.function_call for p in content.parts if p.function_call]
                if not calls:
                    return "".join(p.text for p in content.parts if p.text and not p.thought).strip() or "Done."
                results = [
                    types.Part.from_function_response(
                        name=c.name, response={"result": self.execute(c.name, dict(c.args or {}))}
                    )
                    for c in calls
                ]
                self.history.append(types.Content(role="user", parts=results))
            return f"That took more steps than I'm allowed, {config.USER_TITLE}. I've stopped here."
        except Exception:
            del self.history[start:]  # keep the conversation valid
            raise
