"""
installer/assemble.py — builds the ready-to-install app folder for the
current OS (run by the GitHub Actions workflow on a real Windows / Mac
machine). Output: build/PolicyLookup/{python, ollama, app}.

Everything a user needs is pre-installed here, so the installed app has no
setup step. The only optional download left for the user is the 2 GB AI
model, offered by a button inside the app.
"""

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "build" / "PolicyLookup"
DL = REPO / "build" / "downloads"
IS_WIN = os.name == "nt"
PBS = "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/"
OLLAMA = "https://github.com/ollama/ollama/releases/download/v0.40.16/"
HF = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/"
APP_FILES = ["app.py", "answer.py", "config.py", "explain.py", "glossary.py", "ingest.py", "library.py",
             "search.py", "desktop_launcher.py", "ingest_cli.py", "README.md"]

if IS_WIN:
    PY_ASSET, OLLAMA_ASSET, LOCK = ("cpython-3.12.16%2B20261110-x86_64-pc-windows-msvc-install_only.tar.gz",
                                    "ollama-windows-amd64.zip", "lock-windows-amd64.txt")
else:
    assert sys.platform == "darwin" and platform.machine() == "arm64", "build on an Apple Silicon Mac"
    PY_ASSET, OLLAMA_ASSET, LOCK = ("cpython-3.12.16%2B20261110-aarch64-apple-darwin-install_only.tar.gz",
                                    "ollama-darwin.tgz", "lock-macos-arm64.txt")


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        print("download", url, flush=True)
        urllib.request.urlretrieve(url, dest)
    return dest


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    # 1. private Python with every package installed into it (relocatable, no venv)
    with tarfile.open(fetch(PBS + PY_ASSET, DL / "python.tar.gz")) as t:
        t.extractall(OUT)
    py = OUT / "python" / ("python.exe" if IS_WIN else "bin/python3.12")
    subprocess.run([str(py), "-m", "pip", "install", "--no-warn-script-location", "--only-binary=:all:",
                    "-r", str(REPO / "requirements" / LOCK)], check=True)
    r = subprocess.run([str(py), "-m", "pip", "install", "--no-warn-script-location", "--only-binary=:all:",
                        "tesserocr==2.13.0"])
    if r.returncode != 0 and not IS_WIN:
        # Building on an older macOS than the tesserocr wheel targets: bundle the
        # macOS 15 build anyway. It is used on Macs with macOS 15+; on older Macs
        # the app falls back to the basic OCR automatically.
        site = subprocess.run([str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                              capture_output=True, text=True, check=True).stdout.strip()
        r = subprocess.run([str(py), "-m", "pip", "install", "--no-deps", "--only-binary=:all:", "--upgrade",
                            "--platform", "macosx_15_0_arm64", "--target", site, "tesserocr==2.13.0"])
    print("tesserocr:", "bundled" if r.returncode == 0 else "not available (fallback OCR will be used)")

    # 2. local AI server (CPU build: GPU libraries left out to keep the download small)
    arc = fetch(OLLAMA + OLLAMA_ASSET, DL / OLLAMA_ASSET)
    od = OUT / "ollama"
    od.mkdir()
    if arc.suffix == ".zip":
        with zipfile.ZipFile(arc) as z:
            for n in z.namelist():
                if "/cuda_" not in n and "/rocm" not in n:
                    z.extract(n, od)
    else:
        with tarfile.open(arc) as t:
            t.extractall(od)

    # 3. the app code, settings and bundled models
    app = OUT / "app"
    app.mkdir()
    for f in APP_FILES:
        shutil.copy2(REPO / f, app / f)
    shutil.copytree(REPO / ".streamlit", app / ".streamlit")
    emb = app / "models" / "all-MiniLM-L6-v2"
    for f in ["config.json", "config_sentence_transformers.json", "modules.json", "sentence_bert_config.json",
              "model.safetensors", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
              "vocab.txt", "1_Pooling/config.json"]:
        fetch(HF + f, emb / f)
    (emb / "2_Normalize").mkdir(exist_ok=True)
    fetch("https://github.com/tesseract-ocr/tessdata/raw/main/eng.traineddata",
          app / "models" / "tessdata" / "eng.traineddata")
    # pre-compile so the installed app starts faster and never writes into its own folder
    subprocess.run([str(py), "-m", "compileall", "-q", str(OUT / "app"), str(OUT / "python")])
    print("assembled", OUT)


if __name__ == "__main__":
    main()
