"""
library.py — the document library: manifest, per-file raw-text cache,
adding and removing files. Shared by the Streamlit UI (app.py) and the
bulk command-line ingester (ingest_cli.py), so both behave identically.

No Streamlit import here — this module must be usable headless.
"""

import config  # noqa: F401 — imported first so offline flags are set

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import glossary
import ingest
import search

RAW_TEXT_CACHE_DIR = config.DATA_DIR / "raw_text"
RAW_TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_manifest() -> list[dict]:
    if config.MANIFEST_PATH.exists():
        return json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    return []


def save_manifest(entries: list[dict]) -> None:
    config.MANIFEST_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def raw_text_cache_path(filename: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    return RAW_TEXT_CACHE_DIR / f"{safe}.txt"


def _add_to_manifest(filename: str, result) -> None:
    entries = [e for e in load_manifest() if e["filename"] != filename]
    entries.append({
        "filename": filename,
        "file_type": result.file_type,
        "used_ocr": result.used_ocr,
        "page_count": result.page_count,
        "clause_count": len(result.chunks),
        "date_added": datetime.now().strftime("%Y-%m-%d"),
    })
    save_manifest(entries)
    raw_text_cache_path(filename).write_text(result.raw_text, encoding="utf-8")


def add_file(path: Path, display_name: str | None = None):
    """Extracts, chunks, indexes and registers one file. Re-adding a file
    with the same name replaces its previous version. Returns
    (IngestResult, glossary_conflicts)."""
    result = ingest.ingest_file(Path(path))
    result.filename = display_name or Path(path).name
    search.index_file(result.filename, result.chunks)
    entries = glossary.extract_glossary_from_text(result.raw_text)
    conflicts = glossary.merge_into_global_glossary(entries, result.filename)
    _add_to_manifest(result.filename, result)
    return result, conflicts


def add_uploaded_bytes(name: str, data: bytes):
    """Stages uploaded bytes in DATA_DIR/tmp (on the same drive as the app,
    never the system temp folder), ingests them, then removes the staged copy."""
    staged = config.TMP_DIR / f"{uuid.uuid4().hex}{Path(name).suffix}"
    staged.write_bytes(data)
    try:
        return add_file(staged, display_name=name)
    finally:
        try:
            staged.unlink()
        except OSError:
            pass  # a leftover staging file is harmless


def remove_file(filename: str) -> None:
    entries = [e for e in load_manifest() if e["filename"] != filename]
    save_manifest(entries)
    search.remove_file_from_index(filename)
    cache_path = raw_text_cache_path(filename)
    if cache_path.exists():
        try:
            cache_path.unlink()
        except OSError:
            pass
    # Rebuild the glossary from scratch across whatever files remain —
    # first-definition-wins can't otherwise un-pick a removed file's
    # contribution.
    documents = []
    for e in entries:
        p = raw_text_cache_path(e["filename"])
        if p.exists():
            documents.append((e["filename"], p.read_text(encoding="utf-8")))
    glossary.rebuild_glossary(documents)
