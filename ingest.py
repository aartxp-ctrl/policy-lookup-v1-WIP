"""
ingest.py — file-type detection, text extraction (native PDF text layer /
OCR / DOCX), and structure-aware chunking.

Pipeline for one file: detect_kind -> extract_pages -> strip running
headers/footers -> classify each line's numbering structure -> group lines
into candidate clauses -> merge orphaned headings into their first real
child -> return an IngestResult (chunks + raw cleaned text for glossary.py).
"""

import config  # noqa: F401 — imported first so offline flags are set

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}
SUPPORTED_SUFFIXES = SUPPORTED_IMAGE_SUFFIXES | {".pdf", ".docx"}

MIN_WORDS_FOR_NATIVE_PAGE = 10   # below this, a PDF page is treated as scanned
HEADING_LIKE_MAX_WORDS = 10      # a candidate this short with a deeper child
                                  # immediately after it is a section title,
                                  # not real content — see _merge_candidates


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PageText:
    page_number: int          # 1-indexed physical page
    lines: list[str]


@dataclass
class Chunk:
    text: str                          # verbatim body text — never altered
    citation: str                      # e.g. "Chapter 4 · 4.6.1(a)"
    chapter: str | None                # e.g. "Chapter 4", or None
    clause_number: str                 # e.g. "4.6.1(a)", or "" if un-numbered
    page_number: int                   # 1-indexed physical page of the chunk's start


@dataclass
class IngestResult:
    filename: str
    file_type: str                     # "pdf" | "scanned_pdf" | "image" | "docx"
    used_ocr: bool
    page_count: int
    chunks: list[Chunk]
    raw_text: str                      # full cleaned text, fed to glossary.py


@dataclass
class _Candidate:
    clause_number: str
    chapter: str | None
    page_number: int
    body_lines: list[str] = field(default_factory=list)
    line_pages: list[int] = field(default_factory=list)  # page of each body line
    part_label: str = ""     # "part 2" when a long clause was split
    _in_list: bool = False  # already appended to the candidates list once —
                             # guards against double-appending when an
                             # orphan fragment reuses this object as
                             # `current` rather than starting a new one

    def add(self, line: str, page_number: int) -> None:
        self.body_lines.append(line)
        self.line_pages.append(page_number)

    @property
    def word_count(self) -> int:
        return sum(len(line.split()) for line in self.body_lines)


# ---------------------------------------------------------------------------
# File-type detection
# ---------------------------------------------------------------------------

def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "docx"
    if suffix == ".pdf":
        return "pdf"
    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        return "image"
    raise ValueError(f"Unsupported file type: {path.suffix}")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_tesseract_configured = False


def _configure_tesseract() -> None:
    """Points pytesseract at the tesseract binary explicitly, rather than
    relying on it being on PATH — a double-clicked Run.command/Run.bat
    process doesn't reliably inherit the same PATH a Homebrew/winget
    install added to the user's shell profile."""
    global _tesseract_configured
    if _tesseract_configured:
        return
    import shutil

    import pytesseract

    found = shutil.which("tesseract")
    if not found:
        for candidate in (
            "/opt/homebrew/bin/tesseract",       # Homebrew, Apple Silicon
            "/usr/local/bin/tesseract",          # Homebrew, Intel Mac
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        ):
            if Path(candidate).exists():
                found = candidate
                break
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
    _tesseract_configured = True


_rapidocr_engine = None


def _tesseract_available() -> bool:
    import shutil

    return bool(shutil.which("tesseract")) or any(
        Path(c).exists()
        for c in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract",
                  r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    )


def _tesserocr_available() -> bool:
    try:
        import tesserocr  # noqa: F401
    except Exception:
        return False
    return (config.TESSDATA_DIR / "eng.traineddata").exists()


_engine_cache: str | None = None


def ocr_engine() -> str:
    """The OCR engine actually used on this computer (see config.OCR_ENGINE)."""
    global _engine_cache
    if _engine_cache is None:
        engine = config.OCR_ENGINE
        if engine == "auto":
            if _tesserocr_available():
                engine = "tesserocr"
            elif _tesseract_available():
                engine = "tesseract"
            else:
                engine = "rapidocr"
        _engine_cache = engine
    return _engine_cache


def _ocr_with_tesserocr(pil_image) -> str:
    import tesserocr

    # A fresh API object per page: safe when the UI ingests from several threads.
    with tesserocr.PyTessBaseAPI(path=str(config.TESSDATA_DIR), lang="eng") as api:
        api.SetImage(pil_image)
        return api.GetUTF8Text()


def _ocr_with_tesseract(pil_image) -> str:
    import pytesseract

    _configure_tesseract()
    return pytesseract.image_to_string(pil_image)


def _ocr_with_rapidocr(pil_image) -> str:
    """RapidOCR (ONNX models bundled inside the Python package, so it needs
    no system install and no download). It returns one box per detected
    text fragment; fragments are regrouped into reading-order lines so the
    downstream clause/numbering logic sees the same line structure it would
    get from Tesseract."""
    global _rapidocr_engine
    import numpy as np

    if _rapidocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _rapidocr_engine = RapidOCR()
    result, _ = _rapidocr_engine(np.array(pil_image.convert("RGB")))
    if not result:
        return ""
    boxes = []
    for box, text, _score in result:
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        boxes.append((min(ys), max(ys), min(xs), text))
    boxes.sort(key=lambda b: (b[0], b[2]))
    lines: list[list[tuple]] = []
    for top, bottom, left, text in boxes:
        centre = (top + bottom) / 2
        if lines:
            last = lines[-1]
            l_top = min(b[0] for b in last)
            l_bottom = max(b[1] for b in last)
            if l_top <= centre <= l_bottom:   # same visual line
                last.append((top, bottom, left, text))
                continue
        lines.append([(top, bottom, left, text)])
    return "\n".join(" ".join(b[3] for b in sorted(line, key=lambda b: b[2])) for line in lines)


def _ocr_image(pil_image) -> str:
    engine = ocr_engine()
    if engine == "tesserocr":
        return _ocr_with_tesserocr(pil_image)
    if engine == "tesseract":
        return _ocr_with_tesseract(pil_image)
    return _ocr_with_rapidocr(pil_image)


def extract_pdf_pages(path: Path) -> tuple[list[PageText], bool]:
    """Per page: use the text layer if it's substantial, else rasterize and
    OCR that page. A document can mix native and scanned pages — the file
    is only badged "Scanned PDF" overall if at least one page needed OCR."""
    import pymupdf
    from PIL import Image

    doc = pymupdf.open(str(path))
    pages: list[PageText] = []
    used_ocr_any = False
    try:
        for i in range(doc.page_count):
            page = doc[i]
            text = page.get_text()
            if len(text.split()) < MIN_WORDS_FOR_NATIVE_PAGE:
                pix = page.get_pixmap(dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text = _ocr_image(img)
                # A genuinely blank page (separator, back cover) yields no
                # text even after OCR — it shouldn't badge the whole
                # document as scanned or trigger the OCR accuracy warning.
                if len(text.split()) >= MIN_WORDS_FOR_NATIVE_PAGE:
                    used_ocr_any = True
            pages.append(PageText(page_number=i + 1, lines=text.splitlines()))
    finally:
        doc.close()
    return pages, used_ocr_any


def extract_image_file(path: Path) -> tuple[list[PageText], bool]:
    from PIL import Image

    with Image.open(path) as img:
        text = _ocr_image(img)
    return [PageText(page_number=1, lines=text.splitlines())], True


def extract_docx(path: Path) -> tuple[list[PageText], bool]:
    import docx

    document = docx.Document(str(path))
    lines = [p.text for p in document.paragraphs]
    # .docx carries no native page concept — treated as one logical page.
    return [PageText(page_number=1, lines=lines)], False


# ---------------------------------------------------------------------------
# Header / footer / page-number stripping — generic, frequency-based.
# Nothing here is specific to any one document's actual header text.
# ---------------------------------------------------------------------------

_PAGE_NUMBER_LINE = re.compile(r"^\(?\s*(\d{1,4}|[ivxlcdmIVXLCDM]{1,7})\s*\)?$")


def _is_bare_marker(text: str) -> bool:
    """True for a clause/list marker alone on its line, e.g. "(1)", "(iv)",
    "(a)", "14.", "13-A." — never a header or page number."""
    t = text.strip()
    if re.fullmatch(r"(?i)notes?\s*[:.\-]?", t):
        return True   # a "NOTES" block heading recurs on many pages but is structure
    return bool(
        re.fullmatch(r"\(([a-z]|[ivxlcdm]{1,6}|\d{1,2})\)", t)
        or re.fullmatch(r"\d{1,4}(?:-?[A-Z])?\.", t)
    )


def strip_running_headers_footers(pages: list[PageText]) -> None:
    """Mutates pages in place.

    Two independent, generic passes:
    1. Any line that repeats verbatim (once per page) across a large
       fraction of all pages is a running header/footer — dropped
       everywhere it appears.
    2. A standalone page-number-shaped line (arabic or roman, optionally
       parenthesised) found within the first or last two lines of a page
       is a page number — dropped there. This catches page numbers even
       though their digits differ page to page and so can't be caught by
       exact-text frequency."""
    if len(pages) < 3:
        return  # too few pages for frequency analysis to mean anything

    freq: Counter = Counter()
    for p in pages:
        for norm in {line.strip() for line in p.lines if line.strip()}:
            freq[norm] += 1

    threshold = max(3, int(len(pages) * 0.4))
    # A bare structural marker ("(1)", "(a)", "14.") standing alone on its
    # line repeats on many pages simply because many rules have a sub-rule
    # (1) — it is structure, never a running header, so it is exempt.
    boilerplate = {
        text for text, count in freq.items()
        if count >= threshold and not _is_bare_marker(text)
    }

    for p in pages:
        n = len(p.lines)
        cleaned = []
        for idx, line in enumerate(p.lines):
            norm = line.strip()
            if norm in boilerplate:
                continue
            is_edge = idx < 2 or idx >= n - 2
            if is_edge and norm and _PAGE_NUMBER_LINE.match(norm) and not _is_bare_marker(norm):
                continue
            cleaned.append(line)
        p.lines = cleaned


# ---------------------------------------------------------------------------
# Structure classification and chunking
# ---------------------------------------------------------------------------

_ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_ROMAN_INT_SYMBOLS = [
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
]


def _roman_to_int(s: str) -> int | None:
    s = s.lower()
    total, prev = 0, 0
    for ch in reversed(s):
        val = _ROMAN_VALUES.get(ch)
        if val is None:
            return None
        total += val if val >= prev else -val
        prev = max(prev, val)
    return total


def _int_to_roman(n: int) -> str:
    result = ""
    for value, symbol in _ROMAN_INT_SYMBOLS:
        while n >= value:
            result += symbol
            n -= value
    return result


def _next_in_sequence(shape: str, prev_inner: str) -> str | None:
    """The next marker in a given shape's sequence, e.g. "alpha","b" -> "c",
    "roman","iv" -> "v". Returns None past 'z' or on anything unexpected."""
    if shape == "alpha" and len(prev_inner) == 1 and prev_inner.isalpha() and prev_inner != "z":
        return chr(ord(prev_inner) + 1)
    if shape == "roman":
        val = _roman_to_int(prev_inner)
        return _int_to_roman(val + 1) if val else None
    if shape == "digit" and prev_inner.isdigit():
        return str(int(prev_inner) + 1)
    return None


class _ParenStack:
    """Tracks the currently active chain of parenthetical sub-clause
    markers, e.g. "(a)" then "(i)" nested under it -> suffix "(a)(i)".

    Single letters like "c", "d", "i", "v", "x" are ambiguous — they're
    valid both as the next letter in an alphabetic list and as a Roman
    numeral — so shape can't be guessed from the character alone (that
    misreads plain "(a)(b)(c)" as "(a)(b)" then a nested Roman "(c)",
    i.e. "(b)(c)"). Instead, a new marker is checked against every active
    level's own established sequence, from the innermost out: if it's the
    next value in some ancestor's sequence, it's a sibling continuing at
    that level (and anything deeper is discarded). Only when no ancestor's
    sequence continues does it start a new, deeper level."""

    def __init__(self):
        self.levels: list[tuple[str, str]] = []  # (shape, marker_text)

    def push_or_replace(self, marker: str) -> None:
        inner = marker.strip("()")

        for i in range(len(self.levels) - 1, -1, -1):
            shape, prev_marker = self.levels[i]
            if _next_in_sequence(shape, prev_marker.strip("()")) == inner:
                self.levels = self.levels[: i + 1]
                self.levels[i] = (shape, marker)
                return

        # No ancestor sequence continues with this marker: it starts a new,
        # deeper level. Guess its shape from the character set — a bare
        # "(i)" starting a fresh nested level is overwhelmingly a Roman
        # "one" (nested lists conventionally switch from letters to Roman
        # numerals), not a restart of the alphabet.
        if len(self.levels) >= 3:
            # Real rule books nest at most three levels ("(1)(a)(i)"). A
            # deeper chain only builds up when a marker is missing or out
            # of order in the source (tables, OCR, amendments); treat the
            # marker as a sibling at the deepest level instead of letting
            # the citation grow into "(b)(d)(6)(c)(b)...".
            shape, _ = self.levels[-1]
            self.levels[-1] = (shape, marker)
            return
        if inner.isdigit():
            new_shape = "digit"
        elif inner.lower() == "i" or (len(inner) > 1 and _ROMAN_RE.match(inner)):
            new_shape = "roman"
        else:
            new_shape = "alpha"
        self.levels.append((new_shape, marker))

    def suffix(self) -> str:
        return "".join(m for _, m in self.levels)

    def reset(self) -> None:
        self.levels = []


_BULLET = re.compile(r"^\s*[•▪◦‣·\-–*●○■]\s+")
_CROSS_REF_TAIL = re.compile(
    r"(?i)\b(sub-rules?|rules?|clauses?|sub-clauses?|sections?|sub-sections?|items?|"
    r"paras?|paragraphs?|sub-paras?|and|or|of|in|under|with|to)\s*$"
)
_NOTES_HEADING = re.compile(r"^(?i:notes?)\s*[:.\-]?\s*$")


def _int_base(number: str) -> int | None:
    m = re.match(r"^(\d+)", number or "")
    return int(m.group(1)) if m else None


def _classify_line(line: str):
    """Returns (kind, value, remainder_text) for a structural line, or None
    for plain body text. kind is one of "chapter", "decimal", "integer",
    "parenthetical". remainder_text is whatever followed the number/label
    on the same line (e.g. a heading's title)."""
    stripped = line.strip()
    if not stripped:
        return None

    m = config.RE_LABELED_HEADING.match(stripped)
    if m:
        remainder = stripped[m.end():].strip(" .:-\t")
        # A real heading like "CHAPTER 4" or "Section I" stands alone on
        # its own line, with any title text following on the NEXT line.
        # Requiring an empty remainder here is what keeps this from
        # matching ordinary prose that merely happens to start a wrapped
        # PDF line with one of these words, e.g. "Part of the goods..." or
        # "Chapter or Part of a contract...".
        label = f"{m.group(1).title()} {m.group(2)}"
        if not remainder:
            return ("chapter", label, remainder)
        # "Section 2: Annual Leave", "Article 1 — Passwords": label, a
        # separator, then a short title and nothing else — a heading, unlike
        # prose that merely starts with "Part of the goods ...".
        sep = stripped[m.end():].lstrip()[:1]
        if sep in ":—–-." and len(remainder.split()) <= 8 and not remainder.rstrip().endswith((".", ",", ";")):
            return ("chapter", f"{label} · {remainder.strip(' .:-–—\t')}", "")
        return None

    if _NOTES_HEADING.match(stripped):
        return ("notes", "", "")

    m = config.RE_CAPS_NUMBERED_HEADING.match(stripped)
    if m:
        return ("chapter", f"{m.group(1).strip().title()} {m.group(2)}", "")

    m = config.RE_DECIMAL_NUMBER.match(stripped)
    if m:
        rest = re.sub(r"^\.?\s+", "", stripped[m.end(1):])
        return ("decimal", m.group(1).rstrip("."), rest)

    m = config.RE_PARENTHETICAL.match(stripped)
    if m:
        remainder = stripped[m.end():].strip()
        return ("parenthetical", f"({m.group(1)})", remainder)

    m = config.RE_INTEGER_NUMBER.match(stripped)
    if m:
        rest = stripped[m.end(1):].lstrip(". \t")
        return ("integer", m.group(1), rest)

    return None


def _build_candidates(pages: list[PageText]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    current_chapter: str | None = None
    current_decimal = ""
    paren_stack = _ParenStack()
    # Commentary blocks headed "NOTES" (Army Rules style) number their
    # notes 1, 2, 3 ... again. Without tracking them, "Note 2 to Rule 14"
    # would be cited as a clause "2" — misleading for a reader who goes to
    # look up "rule 2". notes_rule holds the rule a NOTES block belongs to.
    notes_rule: str | None = None
    last_note = 0
    current: _Candidate | None = None
    last_numbered: _Candidate | None = None  # most recent candidate with a
                                              # real (non-empty) clause_number

    def flush():
        nonlocal current, last_numbered
        if current is not None and (current.body_lines or current.clause_number):
            if not current._in_list:
                candidates.append(current)
                current._in_list = True
            if current.clause_number:
                last_numbered = current
        current = None

    def full_number() -> str:
        suffix = paren_stack.suffix()
        return f"{current_decimal}{suffix}" if suffix else current_decimal

    prev_text = ""
    for page in pages:
        for raw_line in page.lines:
            classified = _classify_line(raw_line)
            # "...under the provisions of sub-rule" + next line "(2) or sub-rule (3), ..."
            # — a line-wrapped cross-reference, not a new sub-clause.
            if classified and classified[0] == "parenthetical" and _CROSS_REF_TAIL.search(prev_text):
                classified = None
            if raw_line.strip():
                prev_text = raw_line.strip()

            if classified is None:
                if not raw_line.strip():
                    if current is not None and current.body_lines and current.body_lines[-1] != "":
                        current.add("", page.page_number)   # paragraph break marker
                    continue
                if current is not None and _BULLET.match(raw_line) and current.body_lines and current.body_lines[-1] != "":
                    current.add("", page.page_number)       # a bullet starts a new paragraph
                if current is None:
                    number = full_number()
                    # A fresh body-only fragment with no clause number of
                    # its own (e.g. right after a chapter reset, before
                    # that chapter's first numbered clause) is a
                    # continuation of whatever the most recent real,
                    # citable clause was — not a new, uncitable result of
                    # its own. Attach it there instead of starting an
                    # orphan candidate that could never be properly cited.
                    if not number and last_numbered is not None and last_numbered.chapter == current_chapter:
                        current = last_numbered
                    else:
                        current = _Candidate(
                            clause_number=number,
                            chapter=current_chapter,
                            page_number=page.page_number,
                        )
                current.add(raw_line.strip(), page.page_number)
                continue

            kind, value, remainder = classified

            if kind == "notes":
                if current_decimal:
                    flush()
                    notes_rule = current_decimal.split(" Note ")[0]
                    last_note = 0
                    paren_stack.reset()
                continue

            if kind == "integer" and notes_rule is not None:
                rule_base = _int_base(notes_rule)
                val_base = _int_base(value)
                is_next_note = value.isdigit() and int(value) == last_note + 1
                is_new_rule = val_base is not None and rule_base is not None and (
                    (val_base == rule_base and not value.isdigit())      # "14-A" after rule 14
                    or val_base == rule_base + 1                          # the next rule
                    or (val_base > rule_base and not is_next_note)
                )
                if is_next_note and not is_new_rule:
                    flush()
                    last_note += 1
                    current_decimal = f"{notes_rule} Note {value}"
                    paren_stack.reset()
                    current = _Candidate(
                        clause_number=current_decimal,
                        chapter=current_chapter,
                        page_number=page.page_number,
                    )
                    if remainder:
                        current.add(remainder, page.page_number)
                    continue
                notes_rule = None  # a real rule number: the NOTES block has ended

            if kind == "chapter":
                notes_rule = None
                current_chapter = value
                current_decimal = ""
                paren_stack.reset()
                flush()
                continue

            if kind in ("decimal", "integer"):
                flush()
                current_decimal = value
                paren_stack.reset()
                current = _Candidate(
                    clause_number=current_decimal,
                    chapter=current_chapter,
                    page_number=page.page_number,
                )
                if remainder:
                    current.add(remainder, page.page_number)
                continue

            if kind == "parenthetical":
                if not current_decimal:
                    # A parenthetical only means anything as a sub-point of
                    # an active numbered clause. One with no governing
                    # clause (e.g. "(ii) – (x)" as a page-range reference
                    # in a front-matter index table) is noise that happens
                    # to share the same shape, not real structure — treat
                    # it as plain body text instead.
                    if current is None:
                        if last_numbered is not None and last_numbered.chapter == current_chapter:
                            current = last_numbered
                        else:
                            current = _Candidate(
                                clause_number="",
                                chapter=current_chapter,
                                page_number=page.page_number,
                            )
                    current.add(raw_line.strip(), page.page_number)
                    continue
                flush()
                paren_stack.push_or_replace(value)
                current = _Candidate(
                    clause_number=full_number(),
                    chapter=current_chapter,
                    page_number=page.page_number,
                )
                if remainder:
                    current.add(remainder, page.page_number)
                continue

    flush()
    return candidates


def _structural_depth(number: str) -> int:
    """Nesting depth of a clause number: decimal parts plus parenthetical
    levels, e.g. "4.6" -> 2, "4.6.1" -> 3, "4.6.1(a)" -> 4, "4.6.1(a)(i)"
    -> 5. Used to tell a real child clause/sub-point (deeper) apart from a
    sibling at the same level."""
    if not number:
        return 0
    m = re.match(r"^(\d+(?:\.\d+)*)", number)
    decimal_parts = m.group(1).count(".") + 1 if m else 0
    paren_levels = number.count("(")
    return decimal_parts + paren_levels


def _is_lead_in(cand: _Candidate) -> bool:
    """A short clause that only introduces what follows ("The following
    shall be the age of retirement for Officers :-") — on its own it answers
    nothing, so it merges into its first sub-clause (which holds the actual
    list or table) like a heading does."""
    lines = [l for l in cand.body_lines if l]
    if not lines or cand.word_count > 30:
        return False
    return bool(re.search(r"(:-?|:—|–|—|-)\s*$", lines[-1].strip()))


def _merge_candidates(candidates: list[_Candidate]) -> list[_Candidate]:
    """A heading-like candidate — short (<= HEADING_LIKE_MAX_WORDS) with no
    substantive body of its own, e.g. "4.6  Procurement Committee (PC)"
    immediately before "4.6.1 ..." — is never stored standalone: it merges
    into its first, strictly deeper child, which becomes the addressable
    unit (cascades through multiple empty levels if needed).

    A genuinely short but real clause/list item with no deeper child
    following it (e.g. one enumerated step in a list) is left standing on
    its own rather than merged into its next same-level sibling: this tool
    depends on enumeration/sequence results being individually addressable
    steps, and merging adjacent steps together would defeat that."""
    result: list[_Candidate] = []
    i, n = 0, len(candidates)
    while i < n:
        cand = candidates[i]
        while (
            (cand.word_count < HEADING_LIKE_MAX_WORDS or _is_lead_in(cand))
            and i + 1 < n
            and candidates[i + 1].chapter == cand.chapter
            and _structural_depth(candidates[i + 1].clause_number) > _structural_depth(cand.clause_number)
        ):
            nxt = candidates[i + 1]
            nxt.body_lines = cand.body_lines + nxt.body_lines
            nxt.line_pages = cand.line_pages + nxt.line_pages
            nxt.page_number = cand.page_number
            cand = nxt
            i += 1
        result.append(cand)
        i += 1
    return result


def _candidate_to_chunk(cand: _Candidate) -> Chunk:
    text = " ".join(line for line in cand.body_lines if line).strip()
    citation = f"{cand.chapter} · {cand.clause_number}" if cand.chapter and cand.clause_number else (
        cand.chapter or cand.clause_number or f"Page {cand.page_number}"
    )
    if cand.part_label:
        citation = f"{citation} ({cand.part_label})"
    return Chunk(
        text=text,
        citation=citation,
        chapter=cand.chapter,
        clause_number=cand.clause_number,
        page_number=cand.page_number,
    )


def _exclude_glossary_section_lines(pages: list[PageText]) -> None:
    """Mutates pages in place, dropping lines that fall inside the
    document's own glossary/abbreviations section (as identified by
    glossary.py's own heading-based detector). That content is already
    surfaced through the dedicated glossary feature; left in as a
    searchable chunk, a raw abbreviation table has no real structure to
    chunk on (see build_chunks docstring), collapses into one giant
    catch-all chunk, and — since it names virtually every abbreviation in
    the document — keyword-matches almost any query, drowning out genuine
    results."""
    import glossary as glossary_module

    flat_lines = [line for p in pages for line in p.lines]
    sections = glossary_module._find_glossary_sections(flat_lines)
    if not sections:
        return
    excluded_ids = {id(line) for section in sections for line in section}
    for p in pages:
        p.lines = [line for line in p.lines if id(line) not in excluded_ids]


MAX_CHUNK_WORDS = 350   # longer clauses are split into consecutive parts


UNNUMBERED_TARGET_WORDS = 120   # paragraph-based chunk size for text with no clause numbers


def _split_paragraphs(cand: _Candidate) -> list[_Candidate]:
    """Documents (or stretches) with no clause numbering — memos, policies,
    reports — are split at paragraph breaks into ~120-word chunks, so each
    result is a focused passage rather than a whole page."""
    groups: list[tuple[list[str], list[int]]] = []
    lines, pages_ = cand.body_lines, cand.line_pages or [cand.page_number] * len(cand.body_lines)
    cur_l, cur_p, words = [], [], 0
    for line, pg in zip(lines, pages_):
        if line == "":
            if words >= UNNUMBERED_TARGET_WORDS * 0.5:
                groups.append((cur_l, cur_p)); cur_l, cur_p, words = [], [], 0
            continue
        cur_l.append(line); cur_p.append(pg); words += len(line.split())
        if words >= UNNUMBERED_TARGET_WORDS * 1.6:
            groups.append((cur_l, cur_p)); cur_l, cur_p, words = [], [], 0
    if cur_l:
        if groups and words < UNNUMBERED_TARGET_WORDS * 0.3:
            groups[-1] = (groups[-1][0] + cur_l, groups[-1][1] + cur_p)   # tiny tail joins the previous part
        else:
            groups.append((cur_l, cur_p))
    if len(groups) <= 1:
        return [cand]
    parts = []
    for k, (gl, gp) in enumerate(groups, start=1):
        part = _Candidate(clause_number=cand.clause_number, chapter=cand.chapter, page_number=gp[0],
                          body_lines=gl, line_pages=gp)
        part.part_label = f"part {k}"
        parts.append(part)
    return parts


def _split_oversized(cand: _Candidate) -> list[_Candidate]:
    """Splits a candidate longer than MAX_CHUNK_WORDS into consecutive parts
    at line boundaries (preferring a page break near the limit), so one
    huge unstructured block — a preface, a table, a document with no
    numbering at all — can't become a single result that matches every
    query. Text is never altered or overlapped; each part keeps the
    clause's citation plus "(part k)" and its own starting page."""
    if not cand.clause_number and cand.word_count > UNNUMBERED_TARGET_WORDS * 1.6:
        return [p for part in _split_paragraphs(cand) for p in _split_oversized_numbered(part)]
    return _split_oversized_numbered(cand)


def _split_oversized_numbered(cand: _Candidate) -> list[_Candidate]:
    if cand.word_count <= MAX_CHUNK_WORDS:
        return [cand]
    parts: list[_Candidate] = []
    lines, pages_ = cand.body_lines, cand.line_pages or [cand.page_number] * len(cand.body_lines)
    start, words = 0, 0
    for idx, line in enumerate(lines):
        words += len(line.split())
        at_end = idx == len(lines) - 1
        page_break_next = not at_end and pages_[idx + 1] != pages_[idx]
        if at_end or words >= MAX_CHUNK_WORDS or (page_break_next and words >= MAX_CHUNK_WORDS * 0.6):
            parts.append(_Candidate(
                clause_number=cand.clause_number,
                chapter=cand.chapter,
                page_number=pages_[start],
                body_lines=lines[start:idx + 1],
                line_pages=pages_[start:idx + 1],
            ))
            start, words = idx + 1, 0
    if len(parts) > 1:
        for k, part in enumerate(parts, start=1):
            part.part_label = f"part {k}"
    return parts


def build_chunks(pages: list[PageText]) -> list[Chunk]:
    candidates = _build_candidates(pages)
    merged = _merge_candidates(candidates)
    chunks = []
    for cand in merged:
        if not cand.body_lines:
            continue  # nothing survived (e.g. an empty heading with no child at all)
        for part in _split_oversized(cand):
            chunks.append(_candidate_to_chunk(part))
    return chunks


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def ingest_file(path: Path) -> IngestResult:
    path = Path(path)
    kind = detect_kind(path)

    if kind == "pdf":
        pages, used_ocr = extract_pdf_pages(path)
        file_type = "scanned_pdf" if used_ocr else "pdf"
    elif kind == "image":
        pages, used_ocr = extract_image_file(path)
        file_type = "image"
    elif kind == "docx":
        pages, used_ocr = extract_docx(path)
        file_type = "docx"
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")

    strip_running_headers_footers(pages)
    raw_text = "\n".join(line for p in pages for line in p.lines)  # glossary.py wants the full text
    _exclude_glossary_section_lines(pages)
    chunks = build_chunks(pages)

    return IngestResult(
        filename=path.name,
        file_type=file_type,
        used_ocr=used_ocr,
        page_count=len(pages),
        chunks=chunks,
        raw_text=raw_text,
    )
