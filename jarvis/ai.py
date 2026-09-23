"""Shared Gemini client with automatic model switching, plus vision / web-search helpers.

Free-tier Gemini models are often overloaded (503), rate-limited (429), slow or retired
(404). Every request goes through `run()`, which tries the best available model and on
any such failure immediately moves on to the next one, parking the failed model for a
while so later requests skip it.
"""
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from google import genai
from google.genai import errors, types

from . import config
from .bridge import bridge

# Tried in this order (after GEMINI_MODEL, if set). Only models your key can see are used.
PREFERRED_MODELS = [
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
_available = None      # model names the key can use (None = not fetched yet)
_current = None        # last model that answered
_cooldown = {}         # model -> time it may be retried
_dead = set()          # retired / unknown models


def client():
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=config.REQUEST_TIMEOUT * 1000),
        )
    return _client


def available_models():
    global _available
    if _available is None:
        try:
            _available = [
                m.name.split("/")[-1]
                for m in client().models.list()
                if "generateContent" in (m.supported_actions or [])
            ]
        except Exception:  # noqa: BLE001  (offline or listing not allowed: just try our list)
            _available = []
    return _available


def candidate_models():
    avail = available_models()
    wanted = [config.GEMINI_MODEL] if config.GEMINI_MODEL not in ("", "auto") else []
    # any newer chat models Google adds later, newest first
    extras = sorted((n for n in avail if ("flash" in n or "gemma" in n) and not _SKIP.search(n)), reverse=True)
    names = list(dict.fromkeys(wanted + PREFERRED_MODELS + extras))
    if avail:
        names = [n for n in names if n in avail]
    return [n for n in names if n not in _dead]


def models_to_try():
    """Last working model first, then ready models, then ones still cooling down."""
    now = time.time()
    names = candidate_models()
    ready = [m for m in names if _cooldown.get(m, 0) <= now]
    if _current in ready:
        ready.remove(_current)
        ready.insert(0, _current)
    cooling = sorted((m for m in names if m not in ready), key=lambda m: _cooldown[m])
    return ready + cooling


def model():
    return _current or (models_to_try() or [config.GEMINI_MODEL])[0]


def error_code(err):
    return getattr(err, "code", None)


def is_fatal(err):
    """Errors that switching models can't fix (bad or blocked API key)."""
    if not isinstance(err, errors.APIError):
        return False
    msg = str(err).lower()
    return err.code in (401, 403) or "api key" in msg or "api_key" in msg


def _mark_failed(name, err):
    code = error_code(err)
    if code == 404:
        _dead.add(name)
    else:
        _cooldown[name] = time.time() + (120 if code == 429 else 45)


_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini")
_lock = threading.Lock()


def _note_failure(name, err):
    _mark_failed(name, err)
    reason = {404: "retired", 429: "rate-limited", 503: "overloaded"}.get(error_code(err), "not responding")
    bridge.log(f"{name} {reason}, switching model…", "system")


def run(call, max_models=8):
    """Run call(model_name) and return its result, switching models on failure.

    If the current model hasn't answered within HEDGE_SECONDS, a backup model is
    started in parallel and whichever answers first wins. `call` must have no side
    effects (tools are executed by the caller, never inside it).
    """
    global _current
    order = models_to_try()[:max_models]
    pending = {}
    next_i = 0
    last_err = None

    def launch():
        nonlocal next_i
        name = order[next_i]
        next_i += 1
        pending[_pool.submit(call, name)] = name

    launch()
    while pending:
        done, _ = wait(pending, timeout=config.HEDGE_SECONDS, return_when=FIRST_COMPLETED)
        if not done:  # slow: start a backup model alongside
            if next_i < len(order) and len(pending) < 2:
                launch()
            continue
        for fut in done:
            name = pending.pop(fut)
            try:
                result = fut.result()
            except Exception as err:  # noqa: BLE001
                if is_fatal(err):
                    raise
                last_err = err
                _note_failure(name, err)
                continue
            with _lock:
                if name != _current:
                    if _current:
                        bridge.log(f"Now using {name}", "system")
                    _current = name
                _cooldown.pop(name, None)
            return result  # any other in-flight request finishes in the background and is ignored
        while next_i < len(order) and len(pending) < 1:
            launch()
    raise last_err or RuntimeError("No Gemini models are available for this API key.")


def generate(contents, config_=None):
    return run(lambda name: client().models.generate_content(model=name, contents=contents, config=config_))


def vision(prompt, image_bytes, mime_type="image/png", json_mode=False):
    cfg = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    resp = generate([types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt], cfg)
    return resp.text or ""


def web_search(query):
    """Answer a question with Gemini's Google Search grounding, falling back to DuckDuckGo
    (grounding has a small free-tier quota)."""
    try:
        cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        resp = client().models.generate_content(
            model=model(), contents=f"Search the web and answer concisely with key facts: {query}", config=cfg
        )
        if resp.text:
            return resp.text
    except Exception:  # noqa: BLE001
        pass
    from ddgs import DDGS

    results = DDGS().text(query, max_results=6)
    if not results:
        return "No results found."
    return "\n\n".join(f"{r['title']}\n{r['body']}\n{r['href']}" for r in results)
