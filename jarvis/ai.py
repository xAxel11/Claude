"""Shared Gemini client plus one-shot helpers (vision, web search)."""
import time

from google import genai
from google.genai import errors, types

from . import config

_client = None
_model = None


def client():
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=60_000)
        )
    return _client


def candidate_models():
    models = [config.GEMINI_MODEL] + config.FALLBACK_MODELS
    return list(dict.fromkeys(m for m in models if m))


def model():
    return _model or candidate_models()[0]


def set_model(name):
    global _model
    _model = name


def is_model_missing(err):
    return isinstance(err, errors.ClientError) and err.code == 404


def is_retryable(err):
    return isinstance(err, errors.APIError) and err.code in (429, 500, 503, 504)


def generate(contents, config_=None, retries=1):
    """generate_content with model fallback and a short retry on rate limits."""
    last_err = None
    models = [model()] + [m for m in candidate_models() if m != model()]
    for name in models:
        for attempt in range(retries + 1):
            try:
                resp = client().models.generate_content(model=name, contents=contents, config=config_)
                set_model(name)
                return resp
            except Exception as err:  # noqa: BLE001
                last_err = err
                if is_model_missing(err):
                    break  # try next model
                if is_retryable(err):
                    if attempt < retries:
                        time.sleep(2 * (attempt + 1))
                        continue
                    break  # still overloaded / rate-limited: try next model
                raise
    raise last_err


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
