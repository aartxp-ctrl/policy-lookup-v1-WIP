"""
setup_runtime.py — ONE-TIME, ONLINE setup for a fresh copy of this project
(e.g. downloaded from GitHub). Started by Setup.bat (Windows) or
Setup.command (Mac). Downloads everything the app needs into runtime/ and
models/, inside this folder only. After it finishes, unplug from the
internet: Run.bat / Run.command work fully offline.

Downloads (about 3-4 GB in total):
  - Python packages (exact versions from requirements/lock-<platform>.txt)
  - Ollama (local AI server) and the Llama 3.2 model (~2 GB)
  - the embedding model used for search (~90 MB)
Re-running is safe: finished steps are skipped.
"""

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
RUNTIME = APP_DIR / "runtime"
OLLAMA_VERSION = "v0.40.16"
OLLAMA_MODEL = "llama3.2"
IS_WIN = os.name == "nt"

if IS_WIN:
    PLAT, OLLAMA_ASSET = "windows-amd64", "ollama-windows-amd64.zip"
    OLLAMA_DIR = RUNTIME / "windows" / "ollama"
    OLLAMA_BIN = OLLAMA_DIR / "ollama.exe"
    PIP_PLATFORM = ["--platform", "win_amd64"]
elif sys.platform == "darwin" and platform.machine() == "arm64":
    PLAT, OLLAMA_ASSET = "macos-arm64", "ollama-darwin.tgz"
    OLLAMA_DIR = RUNTIME / "mac" / "ollama"
    OLLAMA_BIN = OLLAMA_DIR / "ollama"
    PIP_PLATFORM = []
else:
    sys.exit("This setup supports 64-bit Intel/AMD Windows and Apple Silicon Macs.")

DOWNLOADS = RUNTIME / "downloads"
WHEELS = RUNTIME / f"wheels-{PLAT}"
LOCK = APP_DIR / "requirements" / f"lock-{PLAT}.txt"


def say(msg: str) -> None:
    print(f"\n[setup] {msg}", flush=True)


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    say(f"Downloading {dest.name} ...")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total and time.time() - last > 2:
                print(f"  {done * 100 // total}%  ({done >> 20} of {total >> 20} MB)", flush=True)
                last = time.time()
    tmp.replace(dest)
    return dest


def step_wheels() -> None:
    marker = WHEELS / ".complete"
    if marker.exists():
        return
    say("Downloading Python packages (one time, ~500 MB) ...")
    WHEELS.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
                    "-d", str(WHEELS), "-r", str(LOCK)] + PIP_PLATFORM, check=True)
    # optional high-accuracy OCR (Tesseract 5 inside a wheel); fine if unavailable
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
                    "-d", str(WHEELS), "tesserocr==2.13.0"] + PIP_PLATFORM)
    marker.write_text("ok\n")


def step_ollama() -> None:
    if OLLAMA_BIN.exists():
        return
    url = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/{OLLAMA_ASSET}"
    archive = download(url, DOWNLOADS / OLLAMA_ASSET)
    say("Unpacking Ollama ...")
    OLLAMA_DIR.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                # GPU (CUDA) libraries are ~2 GB and only help on NVIDIA PCs;
                # the app runs fine on the CPU. Set KEEP_GPU=1 to keep them.
                if "/cuda_" in name and os.environ.get("KEEP_GPU") != "1":
                    continue
                z.extract(name, OLLAMA_DIR)
    else:
        with tarfile.open(archive) as t:
            t.extractall(OLLAMA_DIR)
    if not IS_WIN:
        OLLAMA_BIN.chmod(0o755)


def step_llama() -> None:
    models = RUNTIME / "ollama-models"
    if (models / "manifests" / "registry.ollama.ai" / "library" / OLLAMA_MODEL).exists():
        return
    say(f"Downloading the local AI model ({OLLAMA_MODEL}, ~2 GB) ...")
    env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11435", OLLAMA_MODELS=str(models),
               HOME=str(RUNTIME / "home"), USERPROFILE=str(RUNTIME / "home"))
    (RUNTIME / "home").mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen([str(OLLAMA_BIN), "serve"], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(4)
        subprocess.run([str(OLLAMA_BIN), "pull", OLLAMA_MODEL], env=env, check=True)
    finally:
        server.terminate()


def step_embedding_model() -> None:
    dest = APP_DIR / "models" / "all-MiniLM-L6-v2"
    if dest.exists():
        return
    say("Downloading the search (embedding) model, ~90 MB ...")
    base = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/"
    files = ["config.json", "config_sentence_transformers.json", "modules.json", "sentence_bert_config.json",
             "model.safetensors", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
             "vocab.txt", "1_Pooling/config.json"]
    for f in files:
        download(base + f, dest / f)
    (dest / "2_Normalize").mkdir(exist_ok=True)


def step_tessdata() -> None:
    dest = APP_DIR / "models" / "tessdata" / "eng.traineddata"
    if not dest.exists():
        say("Downloading English OCR data, ~23 MB ...")
        download("https://github.com/tesseract-ocr/tessdata/raw/main/eng.traineddata", dest)


def main() -> None:
    say(f"Setting up for {PLAT}. Everything is saved inside: {APP_DIR}")
    step_wheels()
    step_ollama()
    step_llama()
    step_embedding_model()
    step_tessdata()
    shutil.rmtree(DOWNLOADS, ignore_errors=True)   # installers no longer needed
    say("Done. You can now go offline. Start the app with "
        + ("Run.bat" if IS_WIN else "Run.command") + ".")


if __name__ == "__main__":
    main()
