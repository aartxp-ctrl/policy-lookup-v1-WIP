"""
tests/smoke_test.py — run on the assembled app with the network lock on.
Builds a small PDF (one text page + one scanned page), adds it, searches,
and checks the exact answer sentence. Exits non-zero on any failure.
usage: <bundled python> tests/smoke_test.py <path to app folder>
"""

import io
import os
import sys
import tempfile
from pathlib import Path

app_dir = Path(sys.argv[1]).resolve()
os.environ["POLICY_LOOKUP_DATA_DIR"] = tempfile.mkdtemp(prefix="plk-smoke-")
sys.path.insert(0, str(app_dir))

import config  # noqa: E402  (installs the network lock)
import pymupdf  # noqa: E402

assert config.NETWORK_LOCKED, "network lock is off"
try:
    import urllib.request
    urllib.request.urlopen("https://example.com", timeout=5)
    sys.exit("FAIL: the app process reached the internet")
except Exception as e:
    print("network lock OK:", type(e).__name__)

text_page = ("Section 2: Annual Leave\nEmployees are entitled to 24 days of paid annual leave per year.\n"
             "A maximum of 10 unused days may be carried forward to the next year.")
scan_text = "Section 3: Sick Leave\nA medical certificate is required for sick leave longer than 3 days."
doc = pymupdf.open()
doc.new_page().insert_textbox(pymupdf.Rect(60, 60, 540, 400), text_page, fontsize=11)
tmp = pymupdf.open()
tmp.new_page().insert_textbox(pymupdf.Rect(60, 60, 540, 400), scan_text, fontsize=14)
pix = tmp[0].get_pixmap(dpi=200)
pg = doc.new_page()
pg.insert_image(pg.rect, stream=pix.tobytes("png"))           # image-only page -> OCR
pdf = Path(os.environ["POLICY_LOOKUP_DATA_DIR"]) / "smoke.pdf"
doc.save(pdf)

import answer, ingest, library, search  # noqa: E402,E401

print("OCR engine:", ingest.ocr_engine())
res, _ = library.add_file(pdf)
assert res.used_ocr, "scanned page was not OCR'd"
checks = {"How many days of annual leave?": "24 days",
          "How many leave days can be carried forward?": "10 unused days",
          "When is a medical certificate needed?": "medical certificate"}
for q, want in checks.items():
    out = search.search(q)
    sentence, src = answer.key_sentence(q, out["results"])
    print(f"{q} -> {sentence!r} [{src.citation if src else None}]")
    assert sentence and want in sentence, f"FAIL: expected {want!r}"
print("SMOKE TEST PASSED")
