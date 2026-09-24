"""Which AI provider is active (Gemini or ChatGPT), plus provider-neutral helpers."""
from . import config, gemini_provider, openai_provider
from .bridge import bridge

PROVIDERS = {"gemini": gemini_provider, "openai": openai_provider}
LABELS = {"gemini": "Gemini", "openai": "ChatGPT"}

_provider = None


def configured():
    """Providers that have an API key."""
    return [name for name, mod in PROVIDERS.items() if mod.available()]


def provider():
    global _provider
    if _provider is None:
        options = configured()
        _provider = config.AI_PROVIDER if config.AI_PROVIDER in options else (options[0] if options else "gemini")
    return _provider


def module():
    return PROVIDERS[provider()]


def set_provider(name):
    global _provider
    name = {"chatgpt": "openai", "gpt": "openai", "google": "gemini"}.get(name.lower(), name.lower())
    if name not in PROVIDERS:
        raise ValueError(f"Unknown AI provider {name}")
    if not PROVIDERS[name].available():
        raise ValueError(f"No API key for {LABELS[name]}. Add it to the .env file.")
    _provider = name
    bridge.post("provider", name)
    return LABELS[name]


def current_model():
    return module().pool.current()


def _others():
    return [PROVIDERS[p] for p in configured() if p != provider()]


def vision(prompt, image_bytes, mime_type="image/png", json_mode=False, fast=False):
    """Describe an image with the active provider, falling back to the other one.
    fast=True prefers quick, cheap models (used for finding things to click)."""
    try:
        return module().vision(prompt, image_bytes, mime_type, json_mode, fast)
    except Exception:
        for other in _others():
            try:
                return other.vision(prompt, image_bytes, mime_type, json_mode, fast)
            except Exception:  # noqa: BLE001
                continue
        raise


def web_search(query):
    if gemini_provider.available():
        answer = gemini_provider.web_search(query)
        if answer:
            return answer
    from ddgs import DDGS

    results = DDGS().text(query, max_results=6)
    if not results:
        return "No results found."
    return "\n\n".join(f"{r['title']}\n{r['body']}\n{r['href']}" for r in results)
