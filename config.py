"""
config.py — central configuration for the compliance document lookup tool.

IMPORTANT: this module must be imported FIRST, before chromadb,
sentence-transformers, huggingface_hub or transformers are imported
anywhere else in the app. It sets the offline/telemetry environment
variables those libraries read once, at their own import time — setting
them later has no effect.

Every other module in this app should do `import config` (or
`from config import ...`) before importing any of the above libraries.
"""

import os

# ---------------------------------------------------------------------------
# OFFLINE / TELEMETRY FLAGS — must be set before any downstream library is
# imported. setup_models.py deliberately unsets the HF offline flags for its
# own one-time download step; everywhere else in the app, these stay on.
# ---------------------------------------------------------------------------
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")   # ChromaDB telemetry
os.environ.setdefault("HF_HUB_OFFLINE", "1")              # Hugging Face Hub — no network calls
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")        # transformers / sentence-transformers
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")  # quiet, deterministic tokenizer behaviour

import re
import socket
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# NETWORK LOCK — documents may be restricted, so the app process is not
# allowed to reach anything except this computer. Every outgoing socket
# connection and every DNS lookup is checked: loopback (127.x, ::1,
# localhost — the app's own UI and the local Ollama) and local Unix sockets
# are allowed; anything else is refused before a single byte is sent. This
# holds even if some library tried to phone home. Only the one-time setup
# scripts (which never import this module) go online.
# Set POLICY_LOOKUP_ALLOW_NETWORK=1 to disable (not recommended).
# ---------------------------------------------------------------------------
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", ""}


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    if not isinstance(host, str):
        return True
    h = host.strip("[]").lower()
    return h in _LOCAL_HOSTS or h.startswith("127.") or h.endswith(".localhost")


def _network_lock(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and address and not _is_local(address[0]):
            raise ConnectionRefusedError(f"Offline mode: blocked connection to {address[0]}")
    elif event == "socket.getaddrinfo":
        if not _is_local(args[0]):
            raise socket.gaierror(f"Offline mode: blocked lookup of {args[0]}")


NETWORK_LOCKED = os.environ.get("POLICY_LOOKUP_ALLOW_NETWORK") != "1"
if NETWORK_LOCKED:
    # A proxy (set in the environment or the computer's network settings)
    # would let traffic leave through an allowed local address. Ignore all
    # proxies so every request must resolve and connect directly — which
    # the lock below permits only for this computer.
    for _var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(_var, None)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
if NETWORK_LOCKED and not getattr(sys, "_policy_lookup_lock", False):
    sys.addaudithook(_network_lock)   # audit hooks cannot be removed once added
    sys._policy_lookup_lock = True

# ---------------------------------------------------------------------------
# PATHS — pathlib throughout, no hardcoded separators. BASE_DIR is wherever
# this file lives, so the app works no matter what drive/folder it's run
# from (internal disk, external drive, a colleague's machine after unzip).
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

# Everything the app writes (index, glossary, audit log, temp files) lives
# under DATA_DIR. Override with POLICY_LOOKUP_DATA_DIR to keep a separate
# library (e.g. a demo library with public documents only).
DATA_DIR = Path(os.environ.get("POLICY_LOOKUP_DATA_DIR", BASE_DIR / "data")).resolve()
# Bundled local models ship with the app, separate from user data.
MODELS_DIR = BASE_DIR / "models"
CHROMA_DIR = DATA_DIR / "chroma"
TMP_DIR = DATA_DIR / "tmp"   # uploads are staged here, never in the system temp folder

AUDIT_LOG_PATH = DATA_DIR / "audit_log.jsonl"
GLOSSARY_PATH = DATA_DIR / "glossary.json"
GLOSSARY_SOURCES_PATH = DATA_DIR / "glossary_sources.json"
GLOSSARY_CONFLICTS_PATH = DATA_DIR / "glossary_conflicts.json"
MANIFEST_PATH = DATA_DIR / "file_manifest.json"

for _dir in (DATA_DIR, MODELS_DIR, CHROMA_DIR, TMP_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_LOCAL_DIR = MODELS_DIR / "all-MiniLM-L6-v2"

# The launcher (Run.command) starts its own Ollama from the drive on port
# 11435, so it never clashes with an Ollama app installed on the Mac.
OLLAMA_BASE_URL = os.environ.get("POLICY_LOOKUP_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("POLICY_LOOKUP_OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT_SECONDS = 20          # query classification (falls back silently)
EXPLAIN_TIMEOUT_SECONDS = 120        # plain-language explanation of one clause

# OCR engine for scanned pages and images:
#   "auto"      — best available, in this order (default):
#   "tesserocr" — Tesseract 5 bundled inside a Python package, language data in
#                 models/tessdata — most accurate, runs from this folder
#   "tesseract" — a Tesseract program installed on the computer, if any
#   "rapidocr"  — bundled ONNX OCR; last resort (can miss lines on dense pages)
OCR_ENGINE = os.environ.get("POLICY_LOOKUP_OCR", "auto")
TESSDATA_DIR = BASE_DIR / "models" / "tessdata"

CHROMA_COLLECTION_NAME = "clauses"

# ---------------------------------------------------------------------------
# QUERY MODES
# ---------------------------------------------------------------------------
VALID_MODES = ("definition", "enumeration", "sequence", "permission", "lookup")

RESULT_CAP_BY_MODE = {
    "definition": 2,
    "lookup": 5,
    "enumeration": None,   # uncapped
    "sequence": None,      # uncapped
    "permission": 3,
}

RELEVANCE_THRESHOLD_RATIO = 0.40  # drop results scoring below 40% of top result's score

# ---------------------------------------------------------------------------
# CHUNKING
# ---------------------------------------------------------------------------
MIN_CHUNK_WORDS = 40  # a chunk below this many words of body text merges with its next sibling

# Clause / heading numbering patterns. Used generically by ingest.py so the
# app recognises structure regardless of a document's own numbering scheme.

# Decimal nested numbering: "1.2", "1.2.3", "4.6.1" — deliberately requires
# at least one dot, so it never matches a bare integer heading (see
# RE_INTEGER_NUMBER below, which handles "7." but not "7.2").
RE_DECIMAL_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+){1,5})\.?\s+\S")

# Bare integer heading: "7." (not "7.2" — that's decimal, handled above and
# checked first by callers). Also accepts lettered inserts such as "13-A."
# and "147A." (common in amended rule books), and a number standing alone
# on its line with the rule's title on the next line (e.g. "14." then
# "Termination of service ...") — Word-exported PDFs often lay rules out
# that way.
RE_INTEGER_NUMBER = re.compile(r"^\s*(\d{1,4}(?:-?[A-Z])?)\.(?:\s+\S|\s*$)")

# Labeled headings: "Chapter 4", "Section 4", "Rule 7", "Article 3"
RE_LABELED_HEADING = re.compile(
    r"^\s*(Chapter|Section|Rule|Article|Annexure|Appendix|Part)\s+([A-Za-z0-9\-]+)\b",
    re.IGNORECASE,
)

# All-caps titled block with a dash and a number, standing alone on its
# line — e.g. "PROBLEM STATEMENT - 132", "SCHEDULE - 4". Treated like a
# chapter heading so the numbered points inside each block are cited as
# "Problem Statement 132 · 4(a)" rather than a bare "4(a)".
RE_CAPS_NUMBERED_HEADING = re.compile(r"^\s*([A-Z][A-Z ]{2,40}?)\s*[-–:]\s*(\d{1,4})\s*$")

# Parenthetical sub-clause markers: "(a)", "(iv)", "(2)". Deliberately
# restricted to a single lowercase letter (or lowercase Roman numeral, or
# digits) — a real list marker is always this shape. Anything looser (e.g.
# allowing 2-3 uppercase letters) also matches an inline abbreviation gloss
# like "(PC)" or "(CFA)" that happens to start a line purely because of
# where the PDF's text wrapped, which is not a list marker at all.
# Digits are capped at two: sub-rules never run past (99), while a
# three-digit "(253)" is a page or paragraph cross-reference.
RE_PARENTHETICAL = re.compile(r"^\s*\(([a-z]{1}|[ivxlcdm]{1,6}|\d{1,2})\)\s*")

# A trailing clause-number fragment appended to another number, e.g. the
# "(a)" in "4.6.1(a)"
RE_TRAILING_PARENTHETICAL = re.compile(r"\(([a-z]{1}|[ivxlcdm]{1,6}|\d{1,3})\)$")

# Slash / hyphen compounds to index under each component generically, e.g.
# "PC/CNC", "Buyer/Purchaser", "RFP/NIT", "Supply Order/SO" — matches any
# such compound, not a hardcoded list.
RE_SLASH_HYPHEN_COMPOUND = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\s*[/-]\s*[A-Za-z][A-Za-z0-9]*)+$")

# ---------------------------------------------------------------------------
# GLOSSARY EXTRACTION
# ---------------------------------------------------------------------------
GLOSSARY_SECTION_KEYWORDS = ("abbreviation", "acronym", "glossary")

# A leading serial number on its own line, e.g. "1" / "23." / "317)"
RE_SERIAL_NUMBER_LINE = re.compile(r"^\s*(\d{1,4})[.\)]?\s*$")

# Inline "ABBR - Full Form" / "ABBR : Full Form" / "ABBR – Full Form"
RE_INLINE_ABBREVIATION = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9&./]{0,19})\s*[-:–—]\s*(.{3,200})\s*$"
)

# ---------------------------------------------------------------------------
# FUZZY / HYBRID SEARCH TUNING
# ---------------------------------------------------------------------------
FUZZY_MIN_WORD_LENGTH = 6       # never fuzzy-match tokens shorter than this
FUZZY_MAX_EDIT_DISTANCE = 2     # deliberately tighter than the spec's "2-3
                                 # char edits" — at distance 3 on a 9-char
                                 # typo like "procrment", several unrelated
                                 # words ("document", "provident",
                                 # "placement") are also within budget and
                                 # dilute the intended correction with
                                 # noise; distance 2 still catches the
                                 # genuine correction ("procurement") cleanly
FUZZY_PROTECT_MAX_LENGTH = 3    # tokens this short or shorter (e.g. "PC") are never fuzzy targets

# ---------------------------------------------------------------------------
# SINGLETONS — created lazily so importing config.py never itself triggers
# a model load, a DB connection, or any network call.
# ---------------------------------------------------------------------------
_chroma_client = None
_embedding_model = None


def get_chroma_settings():
    """A chromadb Settings object with telemetry hard-disabled."""
    from chromadb.config import Settings

    return Settings(anonymized_telemetry=False)


def get_chroma_client():
    """Lazily creates (once per process) a persistent, local, telemetry-off
    ChromaDB client backed by data/chroma/."""
    global _chroma_client
    if _chroma_client is None:
        import chromadb

        _chroma_client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=get_chroma_settings(),
        )
    return _chroma_client


def get_embedding_model():
    """Lazily loads (once per process) the local sentence-transformers
    embedding model from disk. Raises a clear error if setup_models.py
    hasn't been run yet — never silently falls back to a network download."""
    global _embedding_model
    if _embedding_model is None:
        if not EMBEDDING_MODEL_LOCAL_DIR.exists():
            raise RuntimeError(
                f"Embedding model not found at {EMBEDDING_MODEL_LOCAL_DIR}. "
                "Run setup_models.py once, with an internet connection, before "
                "first use (or copy the models/ folder from an existing install)."
            )
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(str(EMBEDDING_MODEL_LOCAL_DIR))
    return _embedding_model
