"""
ingest_cli.py — add every supported file in a folder to the library from
the command line (useful for large or scanned PDFs, which can take minutes).

    python ingest_cli.py reference_docs/            # add new files only
    python ingest_cli.py reference_docs/ --force    # re-add everything
"""

import config  # noqa: F401 — imported first so offline flags are set

import argparse
import time
from pathlib import Path

import ingest
import library


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--force", action="store_true", help="re-ingest files already in the library")
    args = ap.parse_args()

    existing = {e["filename"] for e in library.load_manifest()}
    files = sorted(
        p for p in args.folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in ingest.SUPPORTED_SUFFIXES
    )
    if not files:
        print(f"No supported files in {args.folder}")
        return
    for p in files:
        if p.name in existing and not args.force:
            print(f"skip  {p.name} (already in library; use --force to re-add)")
            continue
        t0 = time.time()
        result, conflicts = library.add_file(p)
        ocr = " · OCR used" if result.used_ocr else ""
        print(f"added {p.name}: {result.page_count} pages, {len(result.chunks)} clauses{ocr} "
              f"({time.time() - t0:.0f}s)")
        for c in conflicts:
            print(f"      ⚠ '{c['abbreviation']}' defined differently in {c['conflicting_source']}")


if __name__ == "__main__":
    main()
