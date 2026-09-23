"""Automatic model switching shared by every AI provider.

Free / cheap models are often overloaded, rate-limited, slow or retired. A ModelPool
tries the best available model and on failure immediately moves on to the next one,
parking the failed model for a while. If a model is slow, a backup model is asked in
parallel and whichever answers first wins.
"""
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import config
from .bridge import bridge

_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="llm")


class ModelPool:
    def __init__(self, label, fetch_models, preferred, wanted, keep, error_code, is_fatal):
        self.label = label
        self._fetch = fetch_models      # () -> list of model names the key can use
        self.preferred = preferred      # best first
        self._wanted = wanted           # () -> model the user asked for, or ""
        self._keep = keep               # name -> bool, for models not in `preferred`
        self.error_code = error_code
        self.is_fatal = is_fatal
        self._available = None
        self._current = None
        self._cooldown = {}
        self._dead = set()
        self._lock = threading.Lock()

    def available_models(self):
        if self._available is None:
            try:
                self._available = list(self._fetch())
            except Exception:  # noqa: BLE001  (offline or not allowed: just try our list)
                self._available = []
        return self._available

    def candidates(self):
        avail = self.available_models()
        wanted = self._wanted()
        extras = sorted((n for n in avail if self._keep(n)), reverse=True)  # newer models Google/OpenAI add later
        names = list(dict.fromkeys(([wanted] if wanted else []) + self.preferred + extras))
        if avail:
            names = [n for n in names if n in avail or n == wanted]
        return [n for n in names if n not in self._dead]

    def order(self):
        """Last working model first, then ready models, then ones still cooling down."""
        now = time.time()
        names = self.candidates()
        ready = [m for m in names if self._cooldown.get(m, 0) <= now]
        if self._current in ready:
            ready.remove(self._current)
            ready.insert(0, self._current)
        cooling = sorted((m for m in names if m not in ready), key=lambda m: self._cooldown[m])
        return ready + cooling

    def current(self):
        if self._current:
            return self._current
        order = self.order()
        return order[0] if order else "?"

    def _mark_failed(self, name, err):
        code = self.error_code(err)
        if code == 404:
            self._dead.add(name)
        else:
            self._cooldown[name] = time.time() + (120 if code == 429 else 45)
        reason = {404: "retired", 429: "rate-limited", 503: "overloaded"}.get(code, "not responding")
        bridge.log(f"{self.label}: {name} {reason}, switching model…", "system")

    def run(self, call, max_models=8):
        """Run call(model_name) and return its result. `call` must have no side effects."""
        order = self.order()[:max_models]
        if not order:
            raise RuntimeError(f"No {self.label} models are available for this API key.")
        pending = {}
        next_i = 0
        last_err = None

        def launch():
            nonlocal next_i
            pending[_executor.submit(call, order[next_i])] = order[next_i]
            next_i += 1

        launch()
        while pending:
            done, _ = wait(pending, timeout=config.HEDGE_SECONDS, return_when=FIRST_COMPLETED)
            if not done:  # slow: ask a backup model too
                if next_i < len(order) and len(pending) < 2:
                    launch()
                continue
            for fut in done:
                name = pending.pop(fut)
                try:
                    result = fut.result()
                except Exception as err:  # noqa: BLE001
                    if self.is_fatal(err):
                        raise
                    last_err = err
                    self._mark_failed(name, err)
                    continue
                with self._lock:
                    if name != self._current:
                        if self._current:
                            bridge.log(f"{self.label}: now using {name}", "system")
                        self._current = name
                    self._cooldown.pop(name, None)
                return result  # other in-flight requests finish in the background, ignored
            while next_i < len(order) and not pending:
                launch()
        raise last_err
