"""
desktop_launcher.py — what the installed app runs when you click the
"Policy Lookup" icon (Windows Start menu / desktop, or the Mac app).

Installed layout (built by the installers in installer/):
    <install dir>/python/   private Python with every package pre-installed
    <install dir>/ollama/   local AI server
    <install dir>/app/      this code + bundled models

Your documents, index and the AI model live in your own user folder:
    Windows: %LOCALAPPDATA%\\PolicyLookup      Mac: ~/Library/Application Support/PolicyLookup
No console window, no setup step: it starts the local AI and the app in the
background and opens the app in its own window. Use "Quit" in the app to stop.
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
IS_WIN = os.name == "nt"
APP_PORT, OLLAMA_PORT = 8501, 11435
URL = f"http://127.0.0.1:{APP_PORT}"

if IS_WIN:
    DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PolicyLookup"
    OLLAMA_BIN = ROOT / "ollama" / "ollama.exe"
    PY = ROOT / "python" / "python.exe"
else:
    DATA = Path.home() / "Library" / "Application Support" / "PolicyLookup"
    OLLAMA_BIN = ROOT / "ollama" / "ollama"
    PY = ROOT / "python" / "bin" / "python3.12"
LOGS = DATA / "logs"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0


def up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5):
            return True
    except Exception:
        return False


def open_window() -> None:
    """Opens the app in its own window (Edge/Chrome "app" mode: no tabs or
    address bar) when one of them is installed, else the default browser."""
    candidates = []
    if IS_WIN:
        for base in (os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    else:
        candidates += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                       Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    for exe in candidates:
        if exe.exists():
            subprocess.Popen([str(exe), f"--app={URL}", "--new-window"], creationflags=NO_WINDOW)
            return
    webbrowser.open(URL)


def env() -> dict:
    home = DATA / "home"
    for d in (DATA, LOGS, home, DATA / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    e = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    e.update({
        "NO_PROXY": "*", "no_proxy": "*",
        "POLICY_LOOKUP_DATA_DIR": str(DATA / "library"),
        "POLICY_LOOKUP_OLLAMA_URL": f"http://127.0.0.1:{OLLAMA_PORT}",
        "OLLAMA_HOST": f"127.0.0.1:{OLLAMA_PORT}",
        "OLLAMA_MODELS": str(DATA / "ollama-models"),
        "HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"), "HF_HOME": str(home / ".cache" / "hf"),
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "ANONYMIZED_TELEMETRY": "False",
        "TMPDIR": str(DATA / "tmp"), "TEMP": str(DATA / "tmp"), "TMP": str(DATA / "tmp"),
        "PYTHONNOUSERSITE": "1", "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        "PYTHONDONTWRITEBYTECODE": "1",   # never modify the installed (signed) app files
    })
    if IS_WIN:
        e["USERPROFILE"] = str(home)
    return e


def main() -> None:
    if up(URL):                      # already running: just bring up a window
        open_window()
        return
    e = env()
    log = open(LOGS / "app.log", "a", encoding="utf-8")
    ollama = None
    if OLLAMA_BIN.exists() and not up(f"http://127.0.0.1:{OLLAMA_PORT}/api/tags"):
        ollama = subprocess.Popen([str(OLLAMA_BIN), "serve"], env=e, stdout=log, stderr=log,
                                  creationflags=NO_WINDOW)
    app = subprocess.Popen(
        [str(PY), "-m", "streamlit", "run", str(APP / "app.py"),
         "--server.address", "127.0.0.1", "--server.port", str(APP_PORT),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        env=e, cwd=str(APP), stdout=log, stderr=log, creationflags=NO_WINDOW,
    )
    for _ in range(240):             # first start after install can take ~1 minute
        if up(URL):
            open_window()
            break
        if app.poll() is not None:
            break
        time.sleep(0.5)
    try:
        app.wait()                   # ends when the user clicks "Quit" in the app
    finally:
        if ollama and ollama.poll() is None:
            ollama.terminate()


if __name__ == "__main__":
    main()
