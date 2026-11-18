"""
setup_models.py — ONE-TIME, ONLINE step for a fresh copy of the code
(e.g. cloned from GitHub). Downloads the local embedding model into
models/. After this, the app never needs the internet.

The drive copy already contains models/ and runtime/, so this is not
needed there.

    python setup_models.py
Then, for the local AI (optional):   ollama pull llama3.2
"""

import os

# This script is the one place allowed to go online.
os.environ.pop("HF_HUB_OFFLINE", None)
os.environ.pop("TRANSFORMERS_OFFLINE", None)
os.environ["HF_HUB_OFFLINE"] = "0"

from pathlib import Path  # noqa: E402

from sentence_transformers import SentenceTransformer  # noqa: E402

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
dest = Path(__file__).resolve().parent / "models" / "all-MiniLM-L6-v2"

if dest.exists():
    print(f"Already present: {dest}")
else:
    print(f"Downloading {MODEL} ...")
    SentenceTransformer(MODEL).save(str(dest))
    print(f"Saved to {dest}")
