"""
launch.py — one-click, fully offline launcher for Mac (Apple Silicon) and
Windows (x64). Started by Run.command (Mac) or Run.bat (Windows) using the
Python bundled on this drive — nothing needs to be installed on the
computer, and nothing is written outside this folder.

What it does:
  1. First run only: creates a private Python environment inside
     runtime/ and installs every package from the wheels stored on the
     drive (no internet).
  2. Starts the bundled Ollama (local Llama 3.2) on port 11435, with its
     models and settings kept inside runtime/.
  3. Starts the app and opens it in the browser at http://127.0.0.1:8501.
Closing the window (or Ctrl+C) stops everything.
"""

import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
RUNTIME = APP_DIR / "runtime"
OLLAMA_PORT = 11435
APP_PORT = 8501

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
if IS_MAC and platform.machine() == "arm64":
    PLAT = "macos-arm64"
    OLLAMA_BIN = RUNTIME / "mac" / "ollama" / "ollama"
elif IS_WIN and platform.machine().lower() in ("amd64", "x86_64"):
    PLAT = "windows-amd64"
    OLLAMA_BIN = RUNTIME / "windows" / "ollama" / "ollama.exe"
else:
    sys.exit(f"Unsupported computer: {sys.platform} {platform.machine()}. "
             "This drive has runtimes for Apple Silicon Macs and 64-bit Intel/AMD Windows.")

VENV = RUNTIME / f"venv-{PLAT}"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
WHEELS = RUNTIME / f"wheels-{PLAT}"
LOCK = APP_DIR / "requirements" / f"lock-{PLAT}.txt"
HOME = RUNTIME / "home"                      # private home folder for all caches/settings
DATA_TMP = APP_DIR / "data" / "tmp"


def say(msg: str) -> None:
    print(f"[policy-lookup] {msg}", flush=True)


def private_env() -> dict:
    """Environment for child processes: everything (caches, settings, temp
    files, model downloads that could ever be attempted) points inside this
    folder, and every library is told it is offline."""
    HOME.mkdir(parents=True, exist_ok=True)
    DATA_TMP.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}   # never route via a proxy
    env.update({
        "NO_PROXY": "*", "no_proxy": "*",
        "HOME": str(HOME),
        "XDG_CACHE_HOME": str(HOME / ".cache"),
        "HF_HOME": str(HOME / ".cache" / "huggingface"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "ANONYMIZED_TELEMETRY": "False",
        "TMPDIR": str(DATA_TMP), "TEMP": str(DATA_TMP), "TMP": str(DATA_TMP),
        "PYTHONNOUSERSITE": "1",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        "POLICY_LOOKUP_OLLAMA_URL": f"http://127.0.0.1:{OLLAMA_PORT}",
        "OLLAMA_HOST": f"127.0.0.1:{OLLAMA_PORT}",
        "OLLAMA_MODELS": str(RUNTIME / "ollama-models"),
    })
    if IS_WIN:
        env["USERPROFILE"] = str(HOME)
    return env


def ensure_venv(env: dict) -> None:
    marker = VENV / ".installed"
    if marker.exists():
        return
    say("First run: setting up the offline Python environment on this drive "
        "(takes a few minutes, only once)...")
    if not VENV_PY.exists():
        subprocess.run([sys.executable, "-m", "venv", "--copies", str(VENV)], check=True, env=env)
    subprocess.run(
        [str(VENV_PY), "-m", "pip", "install", "--no-index", "--find-links", str(WHEELS),
         "-r", str(LOCK)],
        check=True, env=env,
    )
    # Best OCR engine (Tesseract 5 bundled in a Python package). Optional:
    # its Mac build needs macOS 15+; without it the app uses a fallback OCR.
    r = subprocess.run([str(VENV_PY), "-m", "pip", "install", "--no-index", "--find-links", str(WHEELS),
                        "tesserocr"], env=env)
    if r.returncode != 0:
        say("Note: high-accuracy OCR isn't available on this computer; scanned PDFs use the basic OCR.")
    marker.write_text("ok\n")
    say("Environment ready.")


def url_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def start_ollama(env: dict):
    if not OLLAMA_BIN.exists():
        say("Bundled Ollama not found — search works, AI explanations stay off.")
        return None
    if url_ok(f"http://127.0.0.1:{OLLAMA_PORT}/api/tags"):
        say("Local AI already running.")
        return None
    say("Starting local AI (Llama 3.2, offline)...")
    log = open(DATA_TMP.parent / "ollama.log", "a", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
    proc = subprocess.Popen([str(OLLAMA_BIN), "serve"], env=env, stdout=log, stderr=log,
                            creationflags=flags)
    for _ in range(60):
        if url_ok(f"http://127.0.0.1:{OLLAMA_PORT}/api/tags"):
            say("Local AI ready.")
            return proc
        time.sleep(0.5)
    say("Local AI did not start (see data/ollama.log) — search still works without it.")
    return proc


def main() -> None:
    env = private_env()
    ensure_venv(env)
    ollama = start_ollama(env)
    say(f"Starting the app — it will open at http://127.0.0.1:{APP_PORT}")
    app = subprocess.Popen(
        [str(VENV_PY), "-m", "streamlit", "run", str(APP_DIR / "app.py"),
         "--server.address", "127.0.0.1", "--server.port", str(APP_PORT),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        env=env, cwd=str(APP_DIR),
    )
    for _ in range(120):
        if url_ok(f"http://127.0.0.1:{APP_PORT}"):
            webbrowser.open(f"http://127.0.0.1:{APP_PORT}")
            break
        time.sleep(0.5)
    say("Running. Keep this window open; close it (or press Ctrl+C) to stop.")
    try:
        app.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in (app, ollama):
            if p and p.poll() is None:
                p.terminate()
        say("Stopped.")


if __name__ == "__main__":
    main()
