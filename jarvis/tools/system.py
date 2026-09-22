import datetime
import json
import os
import platform
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

from .. import ai, config
from ..bridge import bridge
from ._base import tool


def _gui():
    import pyautogui

    pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort
    pyautogui.PAUSE = 0.05
    return pyautogui


def _screenshot_png():
    import mss
    import mss.tools

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        return mss.tools.to_png(shot.rgb, shot.size), shot.size


# ---------------------------------------------------------------- mouse/keyboard
@tool
def get_screen_size() -> str:
    """Return the screen width and height in pixels and the current mouse position."""
    g = _gui()
    w, h = g.size()
    x, y = g.position()
    return f"Screen {w}x{h}, mouse at ({x}, {y})"


@tool
def move_mouse(x: int, y: int, duration: float = 0.3) -> str:
    """Move the mouse cursor to screen pixel coordinates (x, y)."""
    _gui().moveTo(x, y, duration=duration)
    return f"Mouse moved to ({x}, {y})"


@tool
def click(x: int = -1, y: int = -1, button: str = "left", clicks: int = 1) -> str:
    """Click the mouse. If x and y are -1 it clicks at the current position.
    button is 'left', 'right' or 'middle'; clicks=2 for a double click."""
    g = _gui()
    if x >= 0 and y >= 0:
        g.click(x, y, clicks=clicks, button=button, interval=0.1)
    else:
        g.click(clicks=clicks, button=button, interval=0.1)
    return "Clicked."


@tool
def scroll(amount: int) -> str:
    """Scroll the mouse wheel. Positive scrolls up, negative scrolls down (try 5 or -5)."""
    _gui().scroll(amount)
    return "Scrolled."


@tool
def type_text(text: str, press_enter: bool = False) -> str:
    """Type text with the keyboard into whatever is focused. Supports any characters."""
    g = _gui()
    if text.isascii():
        g.write(text, interval=0.01)
    else:  # pyautogui can't type unicode directly; paste it via the clipboard
        bridge.set_clipboard(text)
        g.hotkey("command" if config.OS_NAME == "Darwin" else "ctrl", "v")
    if press_enter:
        g.press("enter")
    return "Typed."


@tool
def press_keys(keys: str) -> str:
    """Press a key or key combination, e.g. 'enter', 'ctrl+c', 'alt+tab', 'win', 'ctrl+shift+t'."""
    parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
    aliases = {"windows": "win", "super": "win", "cmd": "command", "control": "ctrl", "return": "enter"}
    parts = [aliases.get(p, p) for p in parts]
    _gui().hotkey(*parts)
    return f"Pressed {keys}."


# ---------------------------------------------------------------- screen vision
@tool
def look_at_screen(question: str = "Describe what is on the screen.") -> str:
    """Take a screenshot and use vision to answer a question about what's on screen."""
    png, _ = _screenshot_png()
    return ai.vision(question, png)


@tool
def find_on_screen(description: str) -> str:
    """Find a UI element on screen (e.g. 'the Send button', 'the search box') and return
    its pixel coordinates so you can click it."""
    png, _ = _screenshot_png()
    w, h = _gui().size()
    prompt = (
        f"Locate this element on the screenshot: {description}. "
        'Reply as JSON: {"found": true/false, "x": int, "y": int} where x and y are the '
        "element's centre in normalised coordinates from 0 to 1000."
    )
    data = json.loads(ai.vision(prompt, png, json_mode=True))
    if isinstance(data, list):
        data = data[0] if data else {}
    if not data.get("found"):
        return f"Could not find '{description}' on screen."
    x, y = int(data["x"] / 1000 * w), int(data["y"] / 1000 * h)
    return f"Found '{description}' at ({x}, {y})."


@tool
def take_screenshot() -> str:
    """Save a screenshot to disk and show it on the HUD. Returns the file path."""
    png, _ = _screenshot_png()
    path = config.CAPTURES_DIR / f"screenshot_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    path.write_bytes(png)
    bridge.show_image(path)
    return f"Saved screenshot to {path}"


# ---------------------------------------------------------------- apps & system
_LINUX_APPS = {
    "chrome": ["google-chrome", "chromium", "chromium-browser"],
    "browser": ["xdg-open https://google.com"],
    "vscode": ["code"], "vs code": ["code"], "visual studio code": ["code"],
    "terminal": ["x-terminal-emulator", "gnome-terminal", "konsole", "cosmic-term"],
    "files": ["nautilus", "dolphin", "cosmic-files", "xdg-open ~"],
    "calculator": ["gnome-calculator", "kcalc"],
    "settings": ["gnome-control-center", "cosmic-settings"],
}


@tool
def open_application(name: str) -> str:
    """Open an application by name, e.g. 'firefox', 'chrome', 'spotify', 'calculator', 'vscode'."""
    key = name.lower().strip()
    if config.OS_NAME == "Windows":
        g = _gui()  # use the Start menu search, which finds almost anything
        g.press("win")
        time.sleep(0.6)
        g.write(name, interval=0.03)
        time.sleep(0.8)
        g.press("enter")
        return f"Opening {name}."
    if config.OS_NAME == "Darwin":
        subprocess.Popen(["open", "-a", name])
        return f"Opening {name}."
    for cmd in _LINUX_APPS.get(key, []) + [key, key.replace(" ", "-")]:
        exe = cmd.split()[0]
        if shutil.which(exe):
            args = [os.path.expanduser(a) for a in cmd.split()]
            subprocess.Popen(args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opening {name}."
    if shutil.which("gtk-launch"):
        r = subprocess.run(["gtk-launch", key], capture_output=True)
        if r.returncode == 0:
            return f"Opening {name}."
    return f"Couldn't find an app called {name}."


@tool
def open_url(url: str) -> str:
    """Open a website in the default browser."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


@tool
def play_on_youtube(query: str) -> str:
    """Search YouTube for a song or video and open the results."""
    from urllib.parse import quote_plus

    webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    return f"Opened YouTube results for {query}."


@tool
def run_shell_command(command: str) -> str:
    """Run a shell/terminal command on this computer and return its output.
    The user is asked to approve it first."""
    if not bridge.confirm(f"Jarvis wants to run this command:\n\n{command}\n\nAllow it?"):
        return "The user declined to run the command."
    r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr).strip()
    return f"exit code {r.returncode}\n{out[-3000:]}" if out else f"exit code {r.returncode}"


@tool
def get_system_status() -> str:
    """Report CPU, memory, disk, battery and uptime."""
    import psutil

    parts = [
        f"OS {platform.system()} {platform.release()}",
        f"CPU {psutil.cpu_percent(interval=0.5)}%",
        f"RAM {psutil.virtual_memory().percent}%",
        f"Disk {psutil.disk_usage(str(Path.home())).percent}%",
    ]
    battery = psutil.sensors_battery()
    if battery:
        parts.append(f"Battery {battery.percent:.0f}% {'charging' if battery.power_plugged else 'on battery'}")
    uptime = datetime.timedelta(seconds=int(time.time() - psutil.boot_time()))
    parts.append(f"Uptime {uptime}")
    return ", ".join(parts)


@tool
def set_volume(percent: int) -> str:
    """Set the system output volume from 0 to 100."""
    percent = max(0, min(100, percent))
    if config.OS_NAME == "Linux":
        if shutil.which("pactl"):
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"])
        else:
            subprocess.run(["amixer", "-q", "sset", "Master", f"{percent}%"])
    elif config.OS_NAME == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {percent}"])
    else:  # Windows: each key press moves the volume ~2%
        g = _gui()
        g.press("volumedown", presses=50)
        g.press("volumeup", presses=percent // 2)
    return f"Volume set to {percent}%."


@tool
def media_control(action: str) -> str:
    """Control media playback. action: 'playpause', 'next', 'previous' or 'mute'."""
    keys = {"playpause": "playpause", "play": "playpause", "pause": "playpause",
            "next": "nexttrack", "previous": "prevtrack", "mute": "volumemute"}
    _gui().press(keys.get(action.lower(), "playpause"))
    return f"Media: {action}."


@tool
def list_directory(path: str = "~") -> str:
    """List the files and folders in a directory."""
    p = Path(os.path.expanduser(path))
    items = sorted(p.iterdir(), key=lambda i: (not i.is_dir(), i.name.lower()))
    return "\n".join(("[dir] " if i.is_dir() else "") + i.name for i in items[:200]) or "(empty)"


@tool
def read_text_file(path: str) -> str:
    """Read a text file and return its contents (truncated if very long)."""
    return Path(os.path.expanduser(path)).read_text(errors="replace")[:8000]


@tool
def write_text_file(path: str, content: str) -> str:
    """Create or overwrite a text file with the given content."""
    p = Path(os.path.expanduser(path))
    if p.exists() and not bridge.confirm(f"Overwrite existing file?\n\n{p}"):
        return "The user declined to overwrite the file."
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"Wrote {len(content)} characters to {p}"


TOOLS = [
    get_screen_size, move_mouse, click, scroll, type_text, press_keys,
    look_at_screen, find_on_screen, take_screenshot,
    open_application, open_url, play_on_youtube, run_shell_command,
    get_system_status, set_volume, media_control,
    list_directory, read_text_file, write_text_file,
]
