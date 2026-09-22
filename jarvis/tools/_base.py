import functools

from ..bridge import bridge


def tool(fn):
    """Log each tool call to the HUD and turn exceptions into text for the model."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        shown = ", ".join([repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()])
        bridge.log(f"{fn.__name__}({shown[:140]})", "tool")
        try:
            result = fn(*args, **kwargs)
        except Exception as err:  # noqa: BLE001
            result = f"Error: {type(err).__name__}: {err}"
        return "Done." if result is None else result

    return wrapper
