"""Seeing and driving the desktop: precise clicking, typing, windows and clipboard.

Finding things to click, most precise first:
  1. Windows UI Automation: the real buttons, links, fields and menu items of the active
     window, with exact screen rectangles (Windows only).
  2. Two-pass vision: the AI finds the element on a full screenshot, then again on a
     zoomed-in crop around that spot, which lands within a few pixels.
"""
import datetime
import io
import json
import re
import time

from .. import ai, config
from ..bridge import bridge
from ._base import tool

IS_WINDOWS = config.OS_NAME == "Windows"


# ======================================================================== basics
def _gui():
    import pyautogui

    pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort
    pyautogui.PAUSE = 0.03
    return pyautogui


def ensure_desktop():
    """Shrink the fullscreen HUD to its mini pill so it doesn't cover the desktop."""
    bridge.request("mini", True)


def _grab():
    """Screenshot of the main monitor as a PIL image, plus the factor that converts its
    pixels to mouse coordinates (they differ on some high-DPI setups)."""
    import mss
    from PIL import Image

    with (getattr(mss, "MSS", None) or mss.mss)() as sct:
        shot = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    scale = _gui().size()[0] / img.width
    return img, scale


def _jpeg(img, max_width=1600, quality=85):
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def screen_jpeg(max_width=1280):
    """Current screen as JPEG bytes (used to show the AI the result of its actions)."""
    img, _ = _grab()
    return _jpeg(img, max_width, quality=70)


def _focus_target():
    """If Jarvis's own window has keyboard focus, hand it back to the app the user was in,
    so typing goes to the right place (Windows)."""
    if IS_WINDOWS:
        bridge.request("refocus")


def _point_at(x, y):
    """Glide the cursor to (x, y) with a targeting ring, like a person would."""
    g = _gui()
    bridge.request("pointer", int(x), int(y))
    g.moveTo(x, y, duration=0.35, tween=g.easeInOutQuad)


def _parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# ======================================================================== UI Automation (Windows)
_CLICKABLE = {
    "ButtonControl", "HyperlinkControl", "EditControl", "MenuItemControl", "TabItemControl",
    "ListItemControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "TreeItemControl",
    "SplitButtonControl", "DataItemControl", "DocumentControl", "TextControl", "ImageControl",
}
_last_elements = []  # (name, type, x, y) from list_clickable_elements


def _uia_elements(limit=250, seconds=2.5):
    """Named, visible, clickable elements of the foreground window."""
    if not IS_WINDOWS:
        return []
    try:
        import uiautomation as auto
    except ImportError:
        return []
    out = []
    with auto.UIAutomationInitializerInThread():
        root = auto.GetForegroundControl()
        if root is None:
            return []
        start = time.time()
        for ctrl, _depth in auto.WalkControl(root, maxDepth=40):
            if time.time() - start > seconds or len(out) >= limit:
                break
            try:
                ctype = ctrl.ControlTypeName
                name = (ctrl.Name or "").strip()
                if ctype not in _CLICKABLE or not name or ctrl.IsOffscreen:
                    continue
                r = ctrl.BoundingRectangle
                if r.width() <= 2 or r.height() <= 2:
                    continue
                out.append((name[:80], ctype.replace("Control", ""), r.xcenter(), r.ycenter()))
            except Exception:  # noqa: BLE001  (elements can vanish while we walk)
                continue
    return out


_FILLER = {"the", "a", "an", "button", "link", "icon", "tab", "field", "box", "menu", "item", "on", "click"}


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _FILLER]


def _uia_find(description):
    """Best UI Automation match for a description, or None if nothing matches well."""
    quoted = re.findall(r"['\"“”‘’]([^'\"“”‘’]+)['\"“”‘’]", description)
    target = (quoted[0] if quoted else description).lower().strip()
    want = _words(target)
    if not want:
        return None
    best, best_score = None, 0
    for name, ctype, x, y in _uia_elements():
        low = name.lower()
        have = _words(name)
        if low == target or have == want:
            score = 3
        elif target in low and len(target) >= 3:
            score = 2
        elif all(w in have for w in want):
            score = 1.5
        else:
            continue
        if ctype.lower() in description.lower():
            score += 0.5  # e.g. "the Send button" and it's a Button
        if score > best_score:
            best, best_score = (name, ctype, x, y), score
    return best if best_score >= 1.5 else None


# ======================================================================== vision locating
def _ask_point(img, description, zoomed=False):
    prompt = (
        f'Locate this on the {"zoomed-in part of the " if zoomed else ""}screenshot: "{description}".\n'
        'Reply with JSON only: {"found": true/false, "x": int, "y": int, "label": "what you found"} '
        "where x and y are the exact centre of the clickable element in normalised coordinates "
        "0-1000 of THIS image (0,0 = top-left, 1000,1000 = bottom-right). "
        "If several things match, choose the one a person would most likely mean."
    )
    data = _parse_json(ai.vision(prompt, _jpeg(img), "image/jpeg", json_mode=True, fast=True))
    if not data.get("found") or "x" not in data or "y" not in data:
        return None
    return float(data["x"]) / 1000 * img.width, float(data["y"]) / 1000 * img.height, data.get("label", "")


def locate(description):
    """Find an element and return (x, y, how) in mouse coordinates, or None."""
    found = _uia_find(description)
    if found:
        name, ctype, x, y = found
        return x, y, f"{ctype} '{name}'"
    img, scale = _grab()
    first = _ask_point(img, description)
    if not first:
        return None
    x, y, label = first
    # second pass on a zoomed crop around the first guess for pixel precision
    cw, ch = min(img.width, 640), min(img.height, 420)
    left = int(min(max(x - cw / 2, 0), img.width - cw))
    top = int(min(max(y - ch / 2, 0), img.height - ch))
    crop = img.crop((left, top, left + cw, top + ch)).resize((cw * 2, ch * 2))
    second = _ask_point(crop, description, zoomed=True)
    if second:
        x, y = left + second[0] / 2, top + second[1] / 2
        label = second[2] or label
    return x * scale, y * scale, f"'{label or description}' (vision)"


# ======================================================================== tools: look
@tool
def look_at_screen(question: str = "Describe what is on the screen.") -> str:
    """Take a screenshot and answer a question about what's on screen (read text, errors, etc.)."""
    ensure_desktop()
    img, _ = _grab()
    return ai.vision(question, _jpeg(img), "image/jpeg")


@tool
def find_on_screen(description: str) -> str:
    """Find a UI element and return its screen coordinates without clicking it."""
    ensure_desktop()
    found = locate(description)
    if not found:
        return f"Couldn't find '{description}' on screen."
    x, y, how = found
    return f"Found {how} at ({x:.0f}, {y:.0f})."


@tool
def list_clickable_elements(filter_text: str = "") -> str:
    """List the buttons, links, fields and menu items of the active window, numbered, with
    exact positions (Windows only). Use click_element(number) to click one precisely."""
    ensure_desktop()
    global _last_elements
    items = _uia_elements()
    if filter_text:
        items = [i for i in items if filter_text.lower() in i[0].lower()]
    _last_elements = items
    if not items:
        return "No elements found (UI Automation is Windows-only); use click_on with a description instead."
    return "\n".join(f"{n}. [{t}] {name} @({x},{y})" for n, (name, t, x, y) in enumerate(items[:120], 1))


@tool
def take_screenshot() -> str:
    """Save a screenshot to disk and show it on the HUD. Returns the file path."""
    ensure_desktop()
    img, _ = _grab()
    path = config.CAPTURES_DIR / f"screenshot_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    img.save(path)
    bridge.show_image(path)
    return f"Saved screenshot to {path}"


@tool
def get_screen_size() -> str:
    """Return the screen size in pixels, the mouse position and the active window."""
    g = _gui()
    w, h = g.size()
    x, y = g.position()
    return f"Screen {w}x{h}, mouse at ({x}, {y}), active window: {active_window_title() or 'unknown'}"


# ======================================================================== tools: act
@tool
def click_on(description: str, button: str = "left", clicks: int = 1) -> str:
    """Find something on screen by description and click it precisely, e.g.
    "the Send button", "search box", "'Sign in' link", "Chrome icon on the taskbar".
    button: left, right or middle. clicks=2 for double-click. Preferred over raw coordinates."""
    ensure_desktop()
    found = locate(description)
    if not found:
        return f"Couldn't find '{description}' on screen. Try describing it differently or scroll."
    x, y, how = found
    _point_at(x, y)
    _gui().click(x, y, clicks=clicks, button=button, interval=0.08)
    return f"Clicked {how} at ({x:.0f}, {y:.0f})."


@tool
def click_element(number: int, button: str = "left", clicks: int = 1) -> str:
    """Click an element by its number from the last list_clickable_elements call."""
    if not 1 <= number <= len(_last_elements):
        return "No such element number; call list_clickable_elements first."
    name, ctype, x, y = _last_elements[number - 1]
    ensure_desktop()
    _point_at(x, y)
    _gui().click(x, y, clicks=clicks, button=button, interval=0.08)
    return f"Clicked [{ctype}] {name}."


@tool
def type_into(description: str, text: str, press_enter: bool = False, replace: bool = True) -> str:
    """Click a text field found by description, then type into it.
    replace=True clears what was there first."""
    result = click_on(description)
    if result.startswith("Couldn't"):
        return result
    time.sleep(0.15)
    g = _gui()
    if replace:
        g.hotkey("command" if config.OS_NAME == "Darwin" else "ctrl", "a")
    _type(text)
    if press_enter:
        g.press("enter")
    return f"{result} Typed {len(text)} characters{' and pressed Enter' if press_enter else ''}."


@tool
def hover_over(description: str) -> str:
    """Move the mouse over something (to reveal tooltips or menus) without clicking."""
    ensure_desktop()
    found = locate(description)
    if not found:
        return f"Couldn't find '{description}'."
    _point_at(found[0], found[1])
    return f"Hovering over {found[2]}."


@tool
def move_mouse(x: int, y: int) -> str:
    """Glide the mouse cursor to exact screen coordinates (x, y)."""
    ensure_desktop()
    _point_at(x, y)
    return f"Mouse moved to ({x}, {y})."


@tool
def click(x: int = -1, y: int = -1, button: str = "left", clicks: int = 1) -> str:
    """Click at exact screen coordinates (or at the current position if x and y are -1).
    Prefer click_on(description) unless you already know the exact coordinates."""
    ensure_desktop()
    g = _gui()
    if x >= 0 and y >= 0:
        _point_at(x, y)
        g.click(x, y, clicks=clicks, button=button, interval=0.08)
    else:
        g.click(clicks=clicks, button=button, interval=0.08)
    return "Clicked."


@tool
def drag(from_description: str, to_description: str) -> str:
    """Drag something onto something else, both found by description
    (e.g. drag 'report.pdf' to 'the Recycle Bin')."""
    ensure_desktop()
    a = locate(from_description)
    b = locate(to_description)
    if not a or not b:
        return f"Couldn't find {'the start' if not a else 'the target'}."
    g = _gui()
    _point_at(a[0], a[1])
    g.mouseDown()
    g.moveTo(b[0], b[1], duration=0.6, tween=g.easeInOutQuad)
    g.mouseUp()
    return f"Dragged {a[2]} to {b[2]}."


@tool
def scroll(amount: int, x: int = -1, y: int = -1) -> str:
    """Scroll the mouse wheel: positive = up, negative = down (5 is about a page section).
    Optionally scroll over a specific point (x, y)."""
    ensure_desktop()
    g = _gui()
    if x >= 0 and y >= 0:
        g.moveTo(x, y, duration=0.2)
    g.scroll(amount * (120 if IS_WINDOWS else 1))
    return "Scrolled."


def _type(text):
    g = _gui()
    if text.isascii():
        g.write(text, interval=0.008)
    else:  # pyautogui can't type unicode directly; paste it via the clipboard
        _set_clipboard(text)
        g.hotkey("command" if config.OS_NAME == "Darwin" else "ctrl", "v")


@tool
def type_text(text: str, press_enter: bool = False) -> str:
    """Type text with the keyboard into whatever is focused. Supports any characters."""
    ensure_desktop()
    _focus_target()
    _type(text)
    if press_enter:
        _gui().press("enter")
    return "Typed."


@tool
def press_keys(keys: str) -> str:
    """Press a key or shortcut, e.g. 'enter', 'ctrl+c', 'alt+tab', 'win', 'ctrl+shift+t', 'f5'.
    Several in a row: separate with spaces, e.g. 'ctrl+a ctrl+c'."""
    ensure_desktop()
    _focus_target()
    aliases = {"windows": "win", "super": "win", "cmd": "command", "control": "ctrl", "return": "enter",
               "escape": "esc", "del": "delete", "pgdn": "pagedown", "pgup": "pageup"}
    g = _gui()
    for combo in keys.split():
        parts = [aliases.get(k.strip().lower(), k.strip().lower()) for k in combo.split("+") if k.strip()]
        g.hotkey(*parts)
        time.sleep(0.08)
    return f"Pressed {keys}."


@tool
def wait(seconds: float = 1.0) -> str:
    """Wait for something to load (max 15 seconds)."""
    time.sleep(max(0.0, min(seconds, 15.0)))
    return f"Waited {seconds:g} s."


# ======================================================================== windows & clipboard
def active_window_title():
    try:
        if IS_WINDOWS:
            import pygetwindow

            w = pygetwindow.getActiveWindow()
            return w.title if w else ""
        import subprocess

        r = subprocess.run(["xdotool", "getactivewindow", "getwindowname"], capture_output=True, text=True, timeout=1)
        return r.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _windows():
    import pygetwindow

    return [w for w in pygetwindow.getAllWindows() if w.title.strip() and w.width > 50 and "J.A.R.V.I.S" not in w.title]


@tool
def list_windows() -> str:
    """List the open windows by title."""
    try:
        titles = [w.title for w in _windows()]
    except Exception as err:  # noqa: BLE001
        return f"Window listing isn't supported here ({err})."
    return "\n".join(dict.fromkeys(titles)) or "No windows."


def _find_window(title):
    matches = [w for w in _windows() if title.lower() in w.title.lower()]
    if not matches:
        raise ValueError(f"No open window matches '{title}'. Use list_windows to see them.")
    return matches[0]


@tool
def focus_window(title: str) -> str:
    """Bring a window to the front by (part of) its title, e.g. 'Chrome', 'Spotify', 'Notepad'."""
    ensure_desktop()
    w = _find_window(title)
    if w.isMinimized:
        w.restore()
    try:
        w.activate()
    except Exception:  # noqa: BLE001  (Windows sometimes refuses; clicking the title bar works)
        _gui().click(w.left + w.width // 2, w.top + 10)
    return f"Focused '{w.title}'."


@tool
def window_action(title: str, action: str) -> str:
    """Control a window by (part of) its title. action: minimize, maximize, restore or close."""
    w = _find_window(title)
    action = action.lower()
    if action == "close" and not bridge.confirm(f"Close the window '{w.title}'?"):
        return "The user declined."
    getattr(w, {"minimise": "minimize", "maximise": "maximize"}.get(action, action))()
    return f"{action.title()}d '{w.title}'."


def _set_clipboard(text):
    try:
        import pyperclip

        pyperclip.copy(text)
    except Exception:  # noqa: BLE001
        bridge.set_clipboard(text)


@tool
def copy_to_clipboard(text: str) -> str:
    """Put text on the clipboard so the user (or you, with ctrl+v) can paste it."""
    _set_clipboard(text)
    return "Copied to clipboard."


@tool
def read_clipboard() -> str:
    """Return the text currently on the clipboard."""
    import pyperclip

    return pyperclip.paste()[:5000] or "(clipboard is empty)"


@tool
def show_hud() -> str:
    """Bring the full-screen Jarvis HUD back (it shrinks to a mini pill while you work the desktop)."""
    bridge.request("mini", False)
    return "HUD restored."


TOOLS = [
    click_on, type_into, list_clickable_elements, click_element, hover_over, drag,
    look_at_screen, find_on_screen, take_screenshot, get_screen_size,
    move_mouse, click, scroll, type_text, press_keys, wait,
    list_windows, focus_window, window_action, copy_to_clipboard, read_clipboard, show_hud,
]

# Tools after which the AI gets a fresh screenshot to check what happened.
OBSERVE_AFTER = {
    "click_on", "type_into", "click_element", "hover_over", "drag", "move_mouse", "click", "scroll",
    "type_text", "press_keys", "focus_window", "window_action", "open_application", "open_url",
    "play_on_youtube", "wait",
}
