"""OpenAI ChatGPT (paid API key)."""
import base64
import json
import re

from . import config
from .model_pool import ModelPool
from .tools.schema import function_schema

LABEL = "ChatGPT"
PREFERRED = [
    "gpt-5-mini",
    "gpt-5.1-mini",
    "gpt-4.1-mini",
    "gpt-4o-mini",
    "gpt-5",
    "gpt-5.1",
    "gpt-4.1",
    "gpt-4o",
    "gpt-5-nano",
    "gpt-4.1-nano",
]
_SKIP = re.compile(r"audio|realtime|image|tts|transcribe|search|instruct|codex|embedding|moderation|oss|preview|\d{4}-\d{2}-\d{2}")
_client = None


def available():
    return bool(config.OPENAI_API_KEY) and not config.OPENAI_API_KEY.startswith("paste-")


def client():
    global _client
    if _client is None:
        import openai

        _client = openai.OpenAI(api_key=config.OPENAI_API_KEY, timeout=config.REQUEST_TIMEOUT, max_retries=0)
    return _client


def _fetch_models():
    return [m.id for m in client().models.list()]


def error_code(err):
    return getattr(err, "status_code", None)


def _no_credit(err):
    return getattr(err, "code", None) == "insufficient_quota" or "insufficient_quota" in str(err)


def is_fatal(err):
    """Bad key or an account with no credit: switching models won't help."""
    return error_code(err) in (401, 403) or _no_credit(err)


def friendly_error(err):
    if _no_credit(err):
        return (
            f"Your OpenAI account has no API credit, {config.USER_TITLE}. Add billing at "
            "platform.openai.com, or switch me back to Gemini."
        )
    if is_fatal(err):
        return f"OpenAI rejected the API key, {config.USER_TITLE}. Check OPENAI_API_KEY in .env."
    if error_code(err) == 429:
        return f"ChatGPT is rate-limiting us right now, {config.USER_TITLE}. Give me a minute, or switch to Gemini."
    return f"I can't reach ChatGPT right now, {config.USER_TITLE}. Try again in a moment, or switch to Gemini."


pool = ModelPool(
    LABEL,
    _fetch_models,
    PREFERRED,
    wanted=lambda: "" if config.OPENAI_MODEL in ("", "auto") else config.OPENAI_MODEL,
    keep=lambda n: n.startswith("gpt-") and not _SKIP.search(n),
    error_code=error_code,
    is_fatal=is_fatal,
)


def vision(prompt, image_bytes, mime_type="image/png", json_mode=False):
    url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": url}},
    ]}]
    extra = {"response_format": {"type": "json_object"}} if json_mode else {}
    resp = pool.run(lambda m: client().chat.completions.create(model=m, messages=messages, **extra))
    return resp.choices[0].message.content or ""


class Session:
    """One conversation with tool calling; tools run exactly once per call."""

    def __init__(self, system_prompt, tools, execute, seed=()):
        self.tools = [{"type": "function", "function": function_schema(f)} for f in tools]
        self.execute = execute
        self.system = {"role": "system", "content": system_prompt}
        self.history = [{"role": role, "content": text} for role, text in seed]

    def _generate(self, model_name):
        return client().chat.completions.create(
            model=model_name, messages=[self.system] + self.history, tools=self.tools
        )

    def _trim(self, max_items=60):
        if len(self.history) <= max_items:
            return
        for i in range(len(self.history) - max_items // 2, len(self.history)):
            if self.history[i]["role"] == "user":
                self.history = self.history[i:]
                return

    def ask(self, text, max_steps=25):
        self._trim()
        start = len(self.history)
        self.history.append({"role": "user", "content": text})
        try:
            for _ in range(max_steps):
                msg = pool.run(self._generate).choices[0].message
                entry = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                        for tc in msg.tool_calls
                    ]
                self.history.append(entry)
                if not msg.tool_calls:
                    return (msg.content or "").strip() or "Done."
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except ValueError:
                        args = {}
                    result = self.execute(tc.function.name, args)
                    self.history.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
            return f"That took more steps than I'm allowed, {config.USER_TITLE}. I've stopped here."
        except Exception:
            del self.history[start:]
            raise
