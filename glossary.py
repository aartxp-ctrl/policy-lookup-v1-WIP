"""
glossary.py — abbreviation/acronym extraction and management.

Extraction runs only inside sections whose heading names abbreviations,
acronyms, or a glossary — found generically (see _looks_like_heading), not
by hardcoded page numbers or filenames. This keeps both extraction methods
below from firing on ordinary body text, tables of contents, or numbered
lists elsewhere in a document. If no such section is found in a document,
extraction silently returns nothing — no errors, no warnings.

Within an identified section, two independent methods run:

1. TABULAR rows — a serial-number line, followed by the abbreviation on its
   own line, followed by the full form (which may span multiple lines)
   until the next serial-number line appears. This is how abbreviation
   tables typically come out of PDF text extraction once the original
   column layout is lost.

2. INLINE "ABBR - Full Form" / "ABBR : Full Form" lines.

First definition wins, across the whole set of ingested documents (in
ingestion order). Conflicting later definitions are never silently
dropped — they're recorded so the UI can warn the user and show every
definition found.
"""

import json
import re

import config


def _looks_like_heading(line: str) -> bool:
    """Heuristic section-boundary detector. Deliberately conservative about
    what counts as a *new* heading — missing a real boundary just extends
    the current section (harmless), while a false boundary can silently
    truncate it (harmful) or, worse, misclassify running page headers/
    footers ("DPM - 2025") as section breaks. Counting only alphabetic
    words (ignoring page numbers, dashes, dates) is what keeps those
    running headers from tripping this."""
    text = line.strip()
    if not text or len(text) > 100:
        return False
    if config.RE_LABELED_HEADING.match(text):
        return True
    if text.endswith((".", ",", ";")):
        return False
    alpha_words = [w for w in text.split() if any(c.isalpha() for c in w)]
    if len(alpha_words) < 2:
        return False
    letters = [c for w in alpha_words for c in w if c.isalpha()]
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper_ratio >= 0.9


def _find_glossary_sections(lines: list[str]) -> list[list[str]]:
    """Returns the lines that fall under a heading naming abbreviations,
    acronyms or a glossary, up to the next heading of any kind (or end of
    document). A document can have more than one such section (e.g. an
    annexure with its own abbreviation list).

    A line directly preceded by a serial-number line is never treated as a
    heading, even if it happens to be short and all-caps — that shape is
    exactly what a table row's abbreviation cell looks like too (e.g. two
    abbreviation cells that landed on one line during PDF text extraction,
    such as "IEEE IFA"), and without this guard a table row can be
    mistaken for a section-ending heading partway through a real
    abbreviation list."""
    sections: list[list[str]] = []
    current: list[str] | None = None
    last_nonblank = ""
    for line in lines:
        stripped = line.strip()
        if stripped:
            preceded_by_serial = bool(config.RE_SERIAL_NUMBER_LINE.match(last_nonblank))
            if not preceded_by_serial and _looks_like_heading(stripped):
                if current is not None:
                    sections.append(current)
                    current = None
                if any(kw in stripped.lower() for kw in config.GLOSSARY_SECTION_KEYWORDS):
                    current = []
                last_nonblank = stripped
                continue
            last_nonblank = stripped
        if current is not None:
            current.append(line)
    if current is not None:
        sections.append(current)
    return sections


def _extract_tabular(lines: list[str]) -> dict[str, str]:
    """Method 1: serial number -> abbreviation -> full form (collected until
    the next serial number)."""
    found: dict[str, str] = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        if config.RE_SERIAL_NUMBER_LINE.match(line):
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and 0 < len(lines[j].strip()) <= 40:
                abbr = lines[j].strip()
                k = j + 1
                full_parts = []
                while k < n:
                    candidate = lines[k].strip()
                    if config.RE_SERIAL_NUMBER_LINE.match(candidate):
                        break
                    if candidate:
                        full_parts.append(candidate)
                    elif full_parts and k + 1 < n and not lines[k + 1].strip():
                        break  # two consecutive blank lines ends a multi-line full form
                    k += 1
                full_term = " ".join(full_parts).strip()
                # drop page markers that leaked into the cell, e.g. "Principal IFA (viii) (13)"
                full_term = re.sub(r"(\s*\((?:[ivxlcdm]+|\d{1,3})\))+\s*$", "", full_term, flags=re.IGNORECASE).strip()
                if full_term and len(full_term) <= 200:
                    found.setdefault(abbr, full_term)
                i = k
                continue
        i += 1
    return found


def _looks_like_abbreviation_token(token: str) -> bool:
    """Used only by the inline method (method 2), to stop it from matching
    an ordinary hyphenated English word — e.g. "Non-Recurring Engineering"
    or "Computer-aided manufacturing" — that happens to appear inside a
    glossary section as *another* entry's full-form text, wrapped so it
    starts a line. A real abbreviation is short and has more than one
    capital letter, a digit, or an ampersand; an ordinary word in sentence
    case has exactly one capital (its initial)."""
    letters = [c for c in token if c.isalpha()]
    if not letters:
        return False
    if sum(1 for c in letters if c.isupper()) >= 2:
        return True
    return any(c.isdigit() for c in token) or "&" in token


def _extract_inline(lines: list[str]) -> dict[str, str]:
    """Method 2: inline "ABBR - Full Form" style lines."""
    found: dict[str, str] = {}
    for line in lines:
        match = config.RE_INLINE_ABBREVIATION.match(line.strip())
        if match:
            abbr, full_term = match.group(1).strip(), match.group(2).strip()
            if _looks_like_abbreviation_token(abbr):
                found.setdefault(abbr, full_term)
    return found


_PAREN_ABBR_RE = re.compile(r"\(([A-Z][A-Za-z0-9&/\-]{1,11})\)")
_JOIN_WORDS = {"of", "and", "for", "the", "in", "on", "to", "by", "&", "at", "a", "an"}


def _match_initials(words: list[str], abbr: str) -> list[str] | None:
    """Returns the trailing words of `words` whose initials spell `abbr`
    (e.g. ["Competent", "Financial", "Authority"] for "CFA", or
    ["Acceptance", "of", "Necessity"] for "AoN"), else None. Small joining
    words ("of", "and", ...) may be skipped; any other word that doesn't
    fit means it is not a definition."""
    letters = [c for c in abbr if c.isalpha()]
    if len(letters) < 2:
        return None
    i, j = len(words) - 1, len(letters) - 1
    while i >= 0 and j >= 0:
        w = words[i].strip("\"'‘’“”,;:")
        if w and w[0].lower() == letters[j].lower():
            j -= 1
        elif w.lower() not in _JOIN_WORDS:
            return None
        i -= 1
    if j >= 0:
        return None
    phrase = words[i + 1:]
    while phrase and phrase[0].lower() in _JOIN_WORDS:
        phrase = phrase[1:]
    return phrase or None


def _extract_parenthetical(text: str) -> dict[str, str]:
    """Method 3, over the whole document: definitions written in running
    text as "Full Form (ABBR)", e.g. "Competent Financial Authority (CFA)".
    Only accepted when the words right before the brackets really spell the
    abbreviation, so ordinary bracketed text is never picked up. Lets
    documents with no abbreviations table still answer "what is X"."""
    found: dict[str, str] = {}
    flat = " ".join(text.split())
    for m in _PAREN_ABBR_RE.finditer(flat):
        abbr = m.group(1)
        plural = len(abbr) >= 3 and abbr.endswith("s") and abbr[:-1].isupper()
        if plural:
            abbr = abbr[:-1]                       # "IFAs" -> "IFA"
        if not _looks_like_abbreviation_token(abbr) and not (abbr.isupper() and len(abbr) >= 2):
            continue
        before = flat[max(0, m.start() - 160):m.start()].split()[-14:]
        phrase = _match_initials(before, abbr)
        if phrase:
            if plural and len(phrase[-1]) > 3 and phrase[-1].endswith("s"):
                phrase = phrase[:-1] + [phrase[-1][:-1]]   # "Advisors" -> "Advisor"
            full = " ".join(phrase).strip(" ,;:-\"'‘’“”")
            found.setdefault(abbr, full)
    return found


def extract_glossary_from_text(text: str) -> dict[str, str]:
    """Runs the extraction methods over one document's raw text: the table
    and inline methods within every identified abbreviations/glossary
    section, then "Full Form (ABBR)" definitions anywhere in the text.
    Section entries win over running-text ones. Returns a merged
    {abbreviation: full_term} dict ({} if nothing is found)."""
    lines = text.splitlines()
    sections = _find_glossary_sections(lines)
    entries: dict[str, str] = {}
    for section_lines in sections:
        for abbr, full_term in _extract_tabular(section_lines).items():
            entries.setdefault(abbr, full_term)
        for abbr, full_term in _extract_inline(section_lines).items():
            entries.setdefault(abbr, full_term)
    for abbr, full_term in _extract_parenthetical(text).items():
        entries.setdefault(abbr, full_term)
    return entries


# ---------------------------------------------------------------------------
# Persistent, merged, cross-file glossary
# ---------------------------------------------------------------------------

def _load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _save_json(path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_glossary() -> dict[str, str]:
    return _load_json(config.GLOSSARY_PATH, {})


def lookup(term: str) -> dict | None:
    """Case-insensitive lookup of one abbreviation in the merged glossary.
    Returns {"abbr", "full", "source"} or None."""
    if not term:
        return None
    glossary = load_glossary()
    key = term if term in glossary else next(
        (k for k in glossary if k.lower() == term.lower()), None)
    if key is None:
        return None
    sources = _load_json(config.GLOSSARY_SOURCES_PATH, {})
    return {"abbr": key, "full": glossary[key], "source": sources.get(key, "")}


def get_conflicts() -> dict[str, list[dict]]:
    return _load_json(config.GLOSSARY_CONFLICTS_PATH, {})


def merge_into_global_glossary(new_entries: dict[str, str], source_filename: str) -> list[dict]:
    """Merges one document's extracted entries into the persistent global
    glossary (data/glossary.json). First definition — across all files, in
    the order they were ingested — wins. Returns the list of new conflicts
    detected on this call (for a sidebar warning); never raises if there's
    nothing to merge."""
    glossary = load_glossary()
    sources = _load_json(config.GLOSSARY_SOURCES_PATH, {})
    conflicts = get_conflicts()
    new_conflicts = []

    for abbr, full_term in new_entries.items():
        if abbr not in glossary:
            glossary[abbr] = full_term
            sources[abbr] = source_filename
        elif glossary[abbr].strip().lower() != full_term.strip().lower():
            entry = {
                "abbreviation": abbr,
                "used_definition": glossary[abbr],
                "used_source": sources.get(abbr, "unknown"),
                "conflicting_definition": full_term,
                "conflicting_source": source_filename,
            }
            conflicts.setdefault(abbr, [])
            conflicts[abbr].append(entry)
            new_conflicts.append(entry)

    _save_json(config.GLOSSARY_PATH, glossary)
    _save_json(config.GLOSSARY_SOURCES_PATH, sources)
    _save_json(config.GLOSSARY_CONFLICTS_PATH, conflicts)
    return new_conflicts


def rebuild_glossary(documents: list[tuple[str, str]]) -> list[dict]:
    """Rebuilds the entire glossary from scratch given (filename, text)
    pairs for every currently-indexed file, in ingestion order. Needed
    after a file is removed from the library, since first-definition-wins
    otherwise can't un-pick a removed file's contribution. Returns every
    conflict found. Used by library.py."""
    _save_json(config.GLOSSARY_PATH, {})
    _save_json(config.GLOSSARY_SOURCES_PATH, {})
    _save_json(config.GLOSSARY_CONFLICTS_PATH, {})
    all_conflicts = []
    for filename, text in documents:
        entries = extract_glossary_from_text(text)
        all_conflicts.extend(merge_into_global_glossary(entries, filename))
    return all_conflicts
