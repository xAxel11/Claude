"""Build JSON-schema tool definitions from plain Python functions (for OpenAI)."""
import inspect

_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def function_schema(fn):
    sig = inspect.signature(fn)
    props, required = {}, []
    for name, p in sig.parameters.items():
        props[name] = {"type": _TYPES.get(p.annotation, "string")}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    doc = inspect.getdoc(fn) or fn.__name__
    return {
        "name": fn.__name__,
        "description": " ".join(doc.split())[:1000],
        "parameters": {"type": "object", "properties": props, "required": required},
    }


def coerce_args(fn, args):
    """Model arguments arrive as JSON; convert them to what the function expects."""
    params = inspect.signature(fn).parameters
    out = {}
    for key, value in (args or {}).items():
        if key not in params:
            continue
        ann = params[key].annotation
        try:
            if ann is int and not isinstance(value, bool):
                value = int(float(value))
            elif ann is float:
                value = float(value)
            elif ann is bool and isinstance(value, str):
                value = value.lower() in ("true", "1", "yes")
            elif ann is str and not isinstance(value, str):
                value = str(value)
        except (TypeError, ValueError):
            pass
        out[key] = value
    return out
