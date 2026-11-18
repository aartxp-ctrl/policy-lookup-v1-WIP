"""
desktop_launcher.py — what the installed app runs when you click the
"Policy Lookup" icon (Windows Start menu / desktop, or the Mac app).

Installed layout (built by the installers in installer/):
    <install dir>/python/   private Python with every package pre-installed
    <install dir>/ollama/   local AI server
    <install dir>/app/      this code + bundled models

Your documents, index and the AI model live in your own user folder:
    Windows: %LOCALAPPDATA%\\PolicyLookup      Mac: ~/Library/Application Support/PolicyLookup
If the app is on an external drive, they live next to it in "Policy Lookup Data"
instead, so nothing is written to the computer's own disk.
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
    # Portable mode: installed on a drive other than the Windows drive (e.g. an
    # external disk) -> keep documents, index and AI model on that drive too.
    _sys_drive = os.environ.get("SystemDrive", "C:").upper()
    if ROOT.drive and ROOT.drive.upper() != _sys_drive:
        DATA = ROOT / "Policy Lookup Data"
else:
    DATA = Path.home() / "Library" / "Application Support" / "PolicyLookup"
    OLLAMA_BIN = ROOT / "ollama" / "ollama"
    PY = ROOT / "python" / "bin" / "python3.12"
    # Portable mode: the .app sits on an external drive (/Volumes/...) -> keep
    # documents, index and AI model in a folder next to the app on that drive.
    _bundle = ROOT.parent.parent                     # .../Policy Lookup.app
    if _bundle.suffix == ".app" and str(_bundle).startswith("/Volumes/"):
        DATA = _bundle.parent / "Policy Lookup Data"
if os.environ.get("POLICY_LOOKUP_HOME"):              # explicit override
    DATA = Path(os.environ["POLICY_LOOKUP_HOME"])
LOGS = DATA / "logs"
DISK_IMAGE = None                                    # set when data lives in a disk image
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


def _fs_type(path: Path) -> str:
    """Filesystem type of the volume holding `path` (macOS), e.g. 'apfs', 'exfat'."""
    try:
        out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""
    best, kind = "", ""
    for line in out.splitlines():           # "/dev/disk4s1 on /Volumes/X (exfat, local, ...)"
        if " on " not in line or " (" not in line:
            continue
        mnt = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
        if (str(path) + "/").startswith(mnt.rstrip("/") + "/") and len(mnt) > len(best):
            best, kind = mnt, line.rsplit(" (", 1)[1].split(",")[0].strip()
    return kind.lower()


def use_disk_image_if_needed() -> None:
    """Mac external drives are often exFAT/FAT, where the search index's database
    (SQLite) fails with 'attempt to write a readonly database'. In that case keep
    the data inside a growable Mac disk image stored ON THE SAME DRIVE, mounted
    privately (hidden from Finder) while the app runs. Nothing goes on the Mac's disk."""
    global DATA, DISK_IMAGE
    if IS_WIN:
        return
    fs = _fs_type(DATA)
    print(f"[launcher] data folder {DATA} is on filesystem {fs or 'unknown'}", flush=True)
    # Any external drive (/Volumes/...) unless it is a Mac-format disk: use the image.
    if fs in ("apfs", "hfs") or not str(DATA).startswith("/Volumes/"):
        return
    DATA.mkdir(parents=True, exist_ok=True)
    img = DATA / "PolicyLookupData.sparsebundle"
    mnt = Path("/tmp") / f"PolicyLookupData-{os.getuid()}"
    if not os.path.ismount(mnt):
        if not img.exists():
            subprocess.run(["hdiutil", "create", "-size", "500g", "-type", "SPARSEBUNDLE",
                            "-fs", "APFS", "-volname", "PolicyLookupData", str(img)],
                           check=True, capture_output=True, timeout=300)
        mnt.mkdir(parents=True, exist_ok=True)
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-noautoopen", "-owners", "on",
                        "-mountpoint", str(mnt), str(img)],
                       check=True, capture_output=True, timeout=300)
    DISK_IMAGE = mnt
    DATA = mnt
    print(f"[launcher] using data disk image {img} mounted at {mnt}", flush=True)


def detach_disk_image() -> None:
    if DISK_IMAGE and os.path.ismount(DISK_IMAGE):
        for _ in range(5):
            if subprocess.run(["hdiutil", "detach", str(DISK_IMAGE)], capture_output=True).returncode == 0:
                return
            time.sleep(1)
        subprocess.run(["hdiutil", "detach", "-force", str(DISK_IMAGE)], capture_output=True)


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
        "OLLAMA_NO_CLOUD": "1",          # no ollama.com cloud features / model recommendations
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
    LOGS.mkdir(parents=True, exist_ok=True)       # logs stay in the visible data folder
    log = open(LOGS / "app.log", "a", encoding="utf-8")
    sys.stdout = log
    try:
        use_disk_image_if_needed()
    except Exception as exc:
        log.write(f"Could not set up the data disk image: {exc!r}\n"); log.flush()
    e = env()
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
            try:
                ollama.wait(timeout=10)
            except Exception:
                ollama.kill()
        detach_disk_image()


if __name__ == "__main__":
    main()
