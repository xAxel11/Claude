"""Let Jarvis write, run and open code."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config
from ..bridge import bridge
from ._base import tool


def _resolve(filename):
    p = Path(os.path.expanduser(filename))
    if not p.is_absolute():
        p = config.CODE_DIR / p
    return p


def _runner(path):
    ext = path.suffix.lower()
    if ext == ".py":
        return [sys.executable, str(path)]
    if ext in (".js", ".mjs"):
        return ["node", str(path)]
    if ext == ".ps1":
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    if ext in (".bat", ".cmd"):
        return ["cmd", "/c", str(path)]
    if ext == ".sh":
        return ["bash", str(path)]
    if ext in (".html", ".htm"):
        return None  # opened in the browser instead
    raise ValueError(f"I don't know how to run {ext} files.")


@tool
def write_code_file(filename: str, code: str) -> str:
    """Create or overwrite a code file (any language). Relative names are saved in the
    user's Jarvis projects folder, e.g. 'snake/game.py'. Shows the code on screen.
    Returns the full path."""
    path = _resolve(filename)
    inside_projects = config.CODE_DIR in path.parents
    if path.exists() and not inside_projects and not bridge.confirm(f"Overwrite existing file?\n\n{path}"):
        return "The user declined to overwrite the file."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    bridge.show_code(str(path), code)
    return f"Saved {len(code.splitlines())} lines to {path}"


@tool
def run_code_file(filename: str, arguments: str = "", new_window: bool = False) -> str:
    """Run a code file (.py, .js, .ps1, .bat, .sh, .html) and return its output so you can
    check it and fix errors. Use new_window=True for games, GUIs or anything long-running.
    The user approves each run."""
    path = _resolve(filename)
    if not path.exists():
        return f"No such file: {path}"
    cmd = _runner(path)
    if cmd is None:
        import webbrowser

        webbrowser.open(path.as_uri())
        return f"Opened {path.name} in the browser."
    cmd += arguments.split()
    if not bridge.confirm(f"Run this program?\n\n{' '.join(cmd)}"):
        return "The user declined to run it."
    if new_window:
        if config.OS_NAME == "Windows":
            subprocess.Popen(cmd, cwd=path.parent, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(cmd, cwd=path.parent, start_new_session=True)
        return f"Started {path.name} in its own window."
    r = subprocess.run(cmd, cwd=path.parent, capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr).strip()
    return f"exit code {r.returncode}\n{out[-4000:]}" if out else f"exit code {r.returncode} (no output)"


@tool
def open_in_editor(path: str) -> str:
    """Open a file or folder in VS Code (or the default app if VS Code isn't installed)."""
    p = _resolve(path)
    code = shutil.which("code") or shutil.which("code.cmd")
    if code:
        subprocess.Popen([code, str(p)], shell=config.OS_NAME == "Windows")
    elif config.OS_NAME == "Windows":
        os.startfile(p)  # noqa: S606
    elif config.OS_NAME == "Darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])
    return f"Opened {p}"


@tool
def install_python_package(package: str) -> str:
    """pip-install a Python package that a program needs (e.g. 'pygame'). User approves first."""
    if not bridge.confirm(f"Install the Python package '{package}'?"):
        return "The user declined."
    r = subprocess.run([sys.executable, "-m", "pip", "install", package], capture_output=True, text=True, timeout=600)
    return f"exit code {r.returncode}\n{(r.stdout + r.stderr)[-1500:]}"


TOOLS = [write_code_file, run_code_file, open_in_editor, install_python_package]
