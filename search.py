"""
search.py — query intent classification (local Ollama), indexing, hybrid
retrieval (semantic + keyword, with fuzzy/compound/numeric fixes and the
glossary bridge), and audit logging.

Retrieval is fully deterministic. The LLM in classify_query() only ever
decides intent (mode), extracts an entity/file hint, and produces a
cleaned/translated query string — it never sees document content and never
picks which clauses are relevant. Everything after that — embedding,
keyword scoring, fusion, thresholding, ordering — is plain code.
"""

import config  # noqa: F401 — imported first so offline flags are set

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

import glossary

# ---------------------------------------------------------------------------
# Query intent classification (Ollama)
# ---------------------------------------------------------------------------

CLASSIFIER_SYSTEM_PROMPT = """You are a query classifier for a compliance document search tool. Analyse the user's query and return ONLY valid JSON — no other text, no markdown, no explanation:
{
  "mode": one of ["definition","enumeration","sequence","permission","lookup"],
  "entity": main subject being asked about (e.g. "PC", "CNC", "AoN") or null,
  "file_hint": any filename or document reference in the query or null,
  "cleaned_query": query with filler words removed, translated to English if mixed language,
  "language": "english", "hindi", or "mixed"
}

Mode definitions:
definition  — user wants to know what something IS
              ('what is PC', 'PC kya hai', 'define AoN',
               'full form of CNC', 'PC ka matlab')
enumeration — user wants ALL responsibilities/duties/
              functions/activities/powers of something
              ('responsibilities of PC', 'what does CNC do',
               'PC ke kaam', 'functions of TEC')
sequence    — user wants steps or process in order
              ('steps after AoN', 'process for LTE',
               'AoN ke baad kya', 'how to raise STE')
permission  — user wants to know if something is
              allowed/required/mandatory
              ('can CFA chair PC', 'is IFA mandatory',
               'may PC negotiate', 'CFA PC chair kar
               sakta hai kya')
lookup      — anything else, general search
              ('PC and bids', 'L1 nego', '50 lakh rule',
               'who signs CST', 'PC aur TEC mein fark')"""


# llama3.2 (even at temperature 0, reproducibly) collapses "X kya karta
# hai" ("what does X DO") to the same reading as "X kya hai" ("what IS
# X") — it doesn't just pick the wrong mode, it rewrites cleaned_query and
# erases "karta"/"karte" entirely, so there's no cleaned-query signal left
# to recover from downstream. This is a narrow, deterministic correction
# for that one demonstrated blind spot — not a replacement for the LLM's
# classification, which still runs and is used for everything else.
_ENUMERATION_VERB_RE = re.compile(r"\bkarta\b|\bkarte\b|\bkaam\b", re.IGNORECASE)


def _fallback_classification(raw_query: str) -> dict:
    return {
        "mode": "lookup",
        "entity": None,
        "file_hint": None,
        "cleaned_query": raw_query,
        "language": "english",
        "fallback_used": True,
    }


def classify_query(raw_query: str) -> dict:
    """Calls local Ollama to classify query intent. Never blocks the app and
    never surfaces an error to the user: any failure (unreachable, timeout,
    invalid JSON, unrecognised mode) silently falls back to mode="lookup"
    with the original query untouched."""
    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "system": CLASSIFIER_SYSTEM_PROMPT,
                "prompt": raw_query,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=config.OLLAMA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        parsed = json.loads(resp.json().get("response", ""))
        mode = parsed.get("mode")
        if mode not in config.VALID_MODES:
            return _fallback_classification(raw_query)
        if mode == "definition" and _ENUMERATION_VERB_RE.search(raw_query):
            mode = "enumeration"
        return {
            "mode": mode,
            "entity": parsed.get("entity"),
            "file_hint": parsed.get("file_hint"),
            "cleaned_query": parsed.get("cleaned_query") or raw_query,
            "language": parsed.get("language") or "english",
            "fallback_used": False,
        }
    except Exception:
        return _fallback_classification(raw_query)


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_audit(entry: dict) -> None:
    """Appends one JSON line to data/audit_log.jsonl. Never raises — a
    logging failure must never break a search for the user."""
    try:
        entry = {"timestamp": time.time(), **entry}
        with open(config.AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def _get_collection():
    client = config.get_chroma_client()
    # Cosine space so distances translate to an interpretable 0-1
    # similarity — the relevance threshold (drop below 40% of the top
    # result's score) only means something against an actual similarity
    # magnitude, not a bare rank position.
    return client.get_or_create_collection(
        config.CHROMA_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )


def index_file(filename: str, chunks: list) -> None:
    """Embeds and stores every chunk of one file, replacing any existing
    entries for that filename first (so re-ingesting a file is safe)."""
    remove_file_from_index(filename)
    if not chunks:
        return
    collection = _get_collection()
    model = config.get_embedding_model()

    texts = [c.text for c in chunks]
    embeddings = model.encode(texts, show_progress_bar=False).tolist()
    ids = [f"{filename}::{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "filename": filename,
            "citation": c.citation,
            "chapter": c.chapter or "",
            "clause_number": c.clause_number or "",
            "page_number": c.page_number,
            "chunk_index": i,
        }
        for i, c in enumerate(chunks)
    ]
    collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)


def remove_file_from_index(filename: str) -> None:
    collection = _get_collection()
    try:
        collection.delete(where={"filename": filename})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Keyword-side fixes: tokenization, fuzzy matching, truncation expansion,
# compound splitting, numeric normalization. All generic — built from
# whatever is actually in the indexed documents, nothing hardcoded.
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_COMPOUND_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:\s*[/-]\s*[A-Za-z][A-Za-z0-9]*)+")

TRUNCATION_MIN_LENGTH = 4  # e.g. "nego" (4), "proc" (4) — below this, too
                            # ambiguous / risks colliding with abbreviations


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _build_vocabulary(documents: list[str]) -> set[str]:
    vocab = set()
    for doc in documents:
        vocab.update(tokenize(doc))
    return vocab


def _expand_truncations(token: str, vocabulary: set[str]) -> set[str]:
    """"nego" -> {"negotiation", "negotiations", ...} — any vocabulary word
    that starts with the token, built purely from what's indexed."""
    if len(token) < TRUNCATION_MIN_LENGTH:
        return set()
    lowered = token.lower()
    if lowered in {v.lower() for v in vocabulary}:
        return set()  # it's already a real, complete word — not a truncation
    return {v for v in vocabulary if v.lower().startswith(lowered) and len(v) > len(token)}


def _expand_fuzzy(token: str, vocabulary: set[str]) -> set[str]:
    """Up to ~2-3 character edits, only for tokens 6+ chars long — protects
    short abbreviations (PC, CNC, IFA) from ever being "corrected" into
    something else. Only runs when the token has no exact vocabulary match
    (i.e. it might genuinely be a typo, like "procrment") — a token that's
    already a correctly-spelled real word (e.g. "Competent") must not be
    fuzzy-matched too, or it floods scoring with unrelated real words that
    happen to be a few edits away ("compete", "competency", "committed").
    """
    if len(token) < config.FUZZY_MIN_WORD_LENGTH:
        return set()
    lowered = token.lower()
    if lowered in {v.lower() for v in vocabulary}:
        return set()
    from rapidfuzz.distance import Levenshtein

    matches = set()
    for v in vocabulary:
        if len(v) < config.FUZZY_MIN_WORD_LENGTH:
            continue
        if Levenshtein.distance(lowered, v.lower()) <= config.FUZZY_MAX_EDIT_DISTANCE:
            matches.add(v)
    return matches


_SUFFIXES = ("ations", "ation", "ments", "ment", "ions", "ion", "ings", "ing", "ies", "ed", "es", "e", "s", "y")


def _crude_stem(word: str) -> str:
    w = word.lower()
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 5:
            return w[: -len(suf)]
    return w


def _expand_word_forms(token: str, vocabulary: set[str]) -> set[str]:
    """"negotiate" -> {"negotiation", "negotiations", "negotiated"}: other
    grammatical forms of the same word that actually occur in the documents,
    so a question phrased with a verb still keyword-matches clauses that use
    the noun (and vice versa)."""
    stem = _crude_stem(token)
    if len(stem) < 5:
        return set()
    prefix = stem[:-1] if len(stem) >= 7 else None   # "negotiat" -> "negotia*" catches "negotiation"
    out = set()
    for v in vocabulary:
        lv = v.lower()
        if len(v) < 5 or lv == token.lower():
            continue
        if _crude_stem(v) == stem or (prefix and lv.startswith(prefix) and len(lv) <= len(token) + 5):
            out.add(v)
    return out


def expand_query_terms(query: str, vocabulary: set[str], include_word_forms: bool = True) -> set[str]:
    """Every term to search for in keyword scoring: the query's own tokens,
    plus fuzzy corrections, truncation expansions, and the individual parts
    of any slash/hyphen compound written in the query itself."""
    tokens = tokenize(query)
    terms = {t.lower() for t in tokens}
    for tok in tokens:
        if len(tok) <= config.FUZZY_PROTECT_MAX_LENGTH:
            continue  # never touch short abbreviation-shaped tokens
        terms |= {m.lower() for m in _expand_truncations(tok, vocabulary)}
        terms |= {m.lower() for m in _expand_fuzzy(tok, vocabulary)}
        if include_word_forms:
            terms |= {m.lower() for m in _expand_word_forms(tok, vocabulary)}
    for compound in _COMPOUND_RE.findall(query):
        for part in re.split(r"[/-]", compound):
            part = part.strip()
            if part:
                terms.add(part.lower())
    return terms


# --- Numeric normalization (Indian number system, spelled-out numbers,
#     currency symbols, comma formatting) -------------------------------

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "crore": 10_000_000, "crores": 10_000_000,
}
_MULTIPLIER_WORDS = {"lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
                      "lacs": 100_000, "crore": 10_000_000, "crores": 10_000_000,
                      "thousand": 1_000}
_CURRENCY_RE = re.compile(r"[₹]|\brs\.?\b|\binr\b", re.IGNORECASE)
_NUMBER_TOKEN_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?$")
_MIXED_TOKEN_RE = re.compile(r"[A-Za-z]+|\d[\d,]*(?:\.\d+)?")


def _words_to_number(words: list[str]) -> float | None:
    total, current, found = 0, 0, False
    for w in words:
        val = _NUMBER_WORDS.get(w.lower())
        if val is None:
            continue
        found = True
        if val >= 1000:
            current = (current or 1) * val
            total += current
            current = 0
        elif val == 100:
            current = (current or 1) * val
        else:
            current += val
    total += current
    return float(total) if found else None


def extract_numeric_values(text: str) -> tuple[set[float], set[float]]:
    """Every number mentioned in text, normalized to a plain numeric value
    — "50 lakh", "Rs. 50 (Rupees Fifty) lakhs", and "5,000,000" all
    contribute 5000000.0, so any of those phrasings can match each other.

    Returns (all_values, scaled_values). scaled_values is the subset that
    came from applying an Indian-unit or thousand multiplier (i.e. it
    encodes both a number AND a specific unit context) — a match there is
    highly specific. A match only in the full set could just as easily be
    a bare "50" that's actually a page number or clause number elsewhere
    in the text, so it's a much weaker signal and must be scored as one."""
    cleaned = _CURRENCY_RE.sub(" ", text)
    tokens = _MIXED_TOKEN_RE.findall(cleaned)
    values: set[float] = set()
    scaled_values: set[float] = set()
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if _NUMBER_TOKEN_RE.match(tok):
            num = float(tok.replace(",", ""))
            values.add(num)
            if i + 1 < n and tokens[i + 1].lower() in _MULTIPLIER_WORDS:
                scaled = num * _MULTIPLIER_WORDS[tokens[i + 1].lower()]
                values.add(scaled)
                scaled_values.add(scaled)
            i += 1
            continue
        if tok.lower() in _NUMBER_WORDS:
            j = i
            word_seq = []
            while j < n and tokens[j].lower() in _NUMBER_WORDS:
                word_seq.append(tokens[j])
                j += 1
            val = _words_to_number(word_seq)
            if val is not None:
                values.add(val)
                if any(w.lower() in _MULTIPLIER_WORDS for w in word_seq):
                    scaled_values.add(val)
            i = j
            continue
        i += 1
    return values, scaled_values


# ---------------------------------------------------------------------------
# Glossary bridge
# ---------------------------------------------------------------------------

def glossary_bridge_expand(query: str) -> tuple[str, list[tuple[str, str]]]:
    """Expands query terms to their glossary full-form equivalents before
    retrieval. Returns (expanded_query, matches) where matches is
    [(abbreviation, full_term), ...] for the transparent UI notice."""
    glossary_map = glossary.load_glossary()
    matches: list[tuple[str, str]] = []
    seen = set()
    for tok in tokenize(query):
        if tok in glossary_map and tok not in seen:
            seen.add(tok)
            matches.append((tok, glossary_map[tok]))
    expanded = query
    if matches:
        expanded = query + " " + " ".join(full for _, full in matches)
    return expanded, matches


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    citation: str
    chapter: str | None
    clause_number: str
    filename: str
    page_number: int
    text: str
    match_type: str  # "semantic", "keyword", or "semantic+keyword"
    chunk_index: int = 0
    semantic_score: float = 0.0  # cosine similarity of this clause to the query (0-1)


# Below this best-match similarity, nothing in the library is genuinely
# about the query (measured on the reference documents: off-topic queries
# such as "maternity leave policy" or "recipe for biryani" peak at
# 0.21-0.35; real questions score 0.5-0.9). Results are still shown — an
# abbreviation-only query like "L1 nego" can score low semantically yet
# be a valid keyword hit — but the UI flags them as a weak match.
LOW_CONFIDENCE_SIMILARITY = 0.40


def _build_document_frequencies(documents: list[str]) -> tuple[dict[str, int], int]:
    df: dict[str, int] = {}
    for doc in documents:
        for tok in {t.lower() for t in tokenize(doc)}:
            df[tok] = df.get(tok, 0) + 1
    return df, len(documents)


def _idf(term: str, df: dict[str, int], n_docs: int) -> float:
    """A term that's in nearly every chunk of a procurement manual (like
    "procurement" itself) is nearly useless for distinguishing one clause
    from another; a term only a handful of chunks contain (like "AoN") is
    highly specific. Weighting matches by inverse document frequency is
    what lets the 40%-of-top relevance threshold actually discriminate —
    without it, every chunk sharing just the domain's common vocabulary
    clears the bar and enumeration/sequence results balloon to hundreds."""
    freq = df.get(term.lower(), 0)
    return math.log((n_docs + 1) / (freq + 1)) + 1.0


PHRASE_PROXIMITY_WINDOW = 6


def _phrase_proximity_bonus(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Two query terms that appear right next to each other in the query
    ("chair PC") and also close together in a chunk's text are much
    stronger evidence than either term matching somewhere in the chunk on
    its own — it's the difference between a chunk that actually says
    "discretion to chair the PC" and one that separately mentions a
    "Chairperson" role and the "PC" elsewhere. This rewards adjacent query
    words found within a small window of each other in the text,
    regardless of exact phrasing/word order in between."""
    if len(query_tokens) < 2 or not doc_tokens:
        return 0.0
    positions: dict[str, list[int]] = {}
    for idx, tok in enumerate(doc_tokens):
        positions.setdefault(tok.lower(), []).append(idx)

    bonus = 0.0
    for a, b in zip(query_tokens, query_tokens[1:]):
        a, b = a.lower(), b.lower()
        if a == b or a not in positions or b not in positions:
            continue
        best_gap = min(
            abs(pa - pb) for pa in positions[a] for pb in positions[b]
        )
        if best_gap <= PHRASE_PROXIMITY_WINDOW:
            bonus += 3.0 * (1.0 - best_gap / (PHRASE_PROXIMITY_WINDOW + 1))
    return bonus


def _keyword_score(doc_text: str, term_weights: dict[str, float], query_numeric: tuple[set[float], set[float]],
                    df: dict[str, int], n_docs: int, query_tokens: list[str] | None = None) -> float:
    query_all, query_scaled = query_numeric
    if not term_weights and not query_all:
        return 0.0
    doc_tokens = tokenize(doc_text)
    doc_tokens_lower = {t.lower() for t in doc_tokens}
    score = sum(_idf(t, df, n_docs) * w for t, w in term_weights.items() if t in doc_tokens_lower)
    if query_all:
        doc_all, doc_scaled = extract_numeric_values(doc_text)
        if doc_scaled & query_scaled:
            score += 8.0  # e.g. both mention "50 lakh" specifically — highly specific
        elif doc_all & query_all:
            score += 1.5  # a bare shared number (could be a page/clause number) — weak on its own
    if query_tokens:
        score += _phrase_proximity_bonus(query_tokens, doc_tokens)
    return score


def _definition_pattern_boost(text: str, entity: str | None) -> float:
    """A strong, generic signal that this chunk IS the definition of
    `entity`, not just a clause that mentions it: the entity's abbreviation
    appears in parentheses within the first ~100 characters, immediately
    followed by a period — the standard "Full Term (ABBR). <definition
    text>" convention this document (and most compliance manuals) use for
    defined terms. This dominates the fused RRF score on purpose: without
    it, a short defined-term clause can rank below a longer, keyword-rich
    clause that merely uses the term (e.g. an activities list), which is
    exactly what definition mode must not return."""
    if not entity:
        return 0.0
    pattern = re.compile(r"\(" + re.escape(entity) + r"\)\s*\.", re.IGNORECASE)
    return 5.0 if pattern.search(text[:120]) else 0.0


def _correct_query_text(query: str, vocabulary: set[str]) -> str:
    """Best-effort spelling correction of the query text itself, for
    semantic embedding only. A genuine typo ("procrment") isn't a real
    word and embeds poorly; substituting the single closest fuzzy match
    (picked by edit distance, same protections as expand_query_terms —
    never touches short/abbreviation-shaped or already-correct tokens)
    gives the embedding model text it can actually place near the right
    content."""
    from rapidfuzz.distance import Levenshtein

    words = tokenize(query)
    corrected = []
    for w in words:
        if len(w) <= config.FUZZY_PROTECT_MAX_LENGTH:
            corrected.append(w)
            continue
        matches = _expand_fuzzy(w, vocabulary)
        if matches:
            corrected.append(min(matches, key=lambda m: Levenshtein.distance(w.lower(), m.lower())))
        else:
            corrected.append(w)
    return " ".join(corrected) if corrected else query


def retrieve(query: str, mode: str = "lookup", file_filter: list[str] | None = None,
             entity: str | None = None, n_candidates: int = 50) -> list[SearchResult]:
    """Fully deterministic hybrid retrieval: semantic (embeddings) + keyword
    (exact + fuzzy + truncation + compound + numeric), fused with
    reciprocal rank fusion, thresholded, and capped/ordered per mode."""
    collection = _get_collection()
    where = {"filename": {"$in": file_filter}} if file_filter else None

    expanded_query, glossary_matches = glossary_bridge_expand(query)

    # --- fetch the candidate pool this file scope actually holds ---
    pool = collection.get(where=where, include=["documents", "metadatas"])
    if not pool["ids"]:
        return []

    id_to_doc = dict(zip(pool["ids"], pool["documents"]))
    id_to_meta = dict(zip(pool["ids"], pool["metadatas"]))

    vocabulary = _build_vocabulary(pool["documents"])

    # --- semantic similarity (cosine distance -> 0-1 similarity) ---
    # Embedded on the typo-corrected text, not the raw query: a genuine
    # typo like "procrment" embeds poorly on its own (it isn't a real
    # word), so semantic search would otherwise miss content a corrected
    # query matches easily. Keyword-side scoring already corrects
    # separately via expand_query_terms below — this mirrors that fix for
    # the embedding step, so fuzzy correction actually helps semantic
    # search too, not just keyword matching.
    model = config.get_embedding_model()
    embedding_query = _correct_query_text(expanded_query, vocabulary)
    query_embedding = model.encode([embedding_query]).tolist()
    semantic = collection.query(
        query_embeddings=query_embedding,
        n_results=min(n_candidates, len(pool["ids"])),
        where=where,
    )
    semantic_similarity: dict[str, float] = {}
    if semantic["ids"]:
        for id_, dist in zip(semantic["ids"][0], semantic["distances"][0]):
            semantic_similarity[id_] = max(0.0, 1.0 - dist)

    # --- keyword score, normalized against this query's own best match so
    # it's on a comparable 0-1 scale to semantic similarity ---
    # Terms from the user's own query are weighted 1.0; terms that only
    # exist because the glossary bridge appended an abbreviation's full
    # definition are weighted lower. Without that distinction, a query
    # mentioning an abbreviation ends up biased toward whichever clause
    # most closely RESTATES that definition (since the appended text IS
    # the definition), which favours the Definitions-section entry over
    # clauses that actually use the term operationally.
    GLOSSARY_BRIDGE_TERM_WEIGHT = 0.4
    # Other grammatical forms ("negotiate" -> "negotiation") help recall but
    # shouldn't outweigh the words the user actually typed.
    WORD_FORM_TERM_WEIGHT = 0.5
    original_terms = expand_query_terms(query, vocabulary, include_word_forms=False)
    term_weights = {t: 1.0 for t in original_terms}
    for t in expand_query_terms(query, vocabulary) - original_terms:
        term_weights[t] = WORD_FORM_TERM_WEIGHT
    if glossary_matches:
        bridge_phrase = " ".join(full for _, full in glossary_matches)
        for t in expand_query_terms(bridge_phrase, vocabulary):
            term_weights.setdefault(t, GLOSSARY_BRIDGE_TERM_WEIGHT)
    query_numeric = extract_numeric_values(expanded_query)
    df, n_docs = _build_document_frequencies(pool["documents"])
    query_token_list = tokenize(expanded_query)
    raw_keyword_scores = {
        id_: _keyword_score(id_to_doc[id_], term_weights, query_numeric, df, n_docs, query_token_list)
        for id_ in pool["ids"]
    }
    max_kw = max(raw_keyword_scores.values(), default=0.0)
    keyword_similarity = {
        id_: (score / max_kw) for id_, score in raw_keyword_scores.items() if score > 0
    }

    # --- fusion: sum of both signals (a chunk strong on either counts;
    # strong on both ranks highest) ---
    combined: dict[str, float] = {}
    for id_ in set(semantic_similarity) | set(keyword_similarity):
        combined[id_] = semantic_similarity.get(id_, 0.0) + keyword_similarity.get(id_, 0.0)
    if not combined:
        return []

    if mode == "definition" and entity:
        for id_ in combined:
            combined[id_] += _definition_pattern_boost(id_to_doc[id_], entity)

    # Tie-break deterministically (earlier chunk wins) rather than by
    # insertion order — Python's set/dict iteration order is randomized per
    # process (PYTHONHASHSEED), so an exact score tie (common with IDF
    # keyword scoring, e.g. several chunks sharing the same two matched
    # terms) would otherwise make results non-reproducible run to run,
    # which an audited compliance tool can't allow.
    ranked = sorted(
        combined.items(),
        key=lambda pair: (pair[1], -id_to_meta[pair[0]]["chunk_index"]),
        reverse=True,
    )

    # --- relevance threshold: drop below 40% of the top result's score ---
    top_score = ranked[0][1]
    cutoff = top_score * config.RELEVANCE_THRESHOLD_RATIO
    survivors = [(id_, score) for id_, score in ranked if score >= cutoff]

    results = []
    for id_, _score in survivors:
        meta = id_to_meta[id_]
        in_semantic = id_ in semantic_similarity
        in_keyword = id_ in keyword_similarity
        match_type = "semantic+keyword" if in_semantic and in_keyword else (
            "semantic" if in_semantic else "keyword"
        )
        results.append(SearchResult(
            citation=meta["citation"],
            chapter=meta["chapter"] or None,
            clause_number=meta["clause_number"],
            filename=meta["filename"],
            page_number=meta["page_number"],
            text=id_to_doc[id_],
            match_type=match_type,
            chunk_index=meta["chunk_index"],
            semantic_score=round(semantic_similarity.get(id_, 0.0), 3),
        ))

    # --- mode-based ordering and cap ---
    if mode in ("enumeration", "sequence"):
        results.sort(key=lambda r: (r.filename, r.chunk_index))
        return results  # uncapped, document order

    cap = config.RESULT_CAP_BY_MODE.get(mode, config.RESULT_CAP_BY_MODE["lookup"])
    return results[:cap] if cap else results


_DEFINITION_QUESTION_RE = re.compile(
    r"\b(what\s+(is|are|does|do)|what's|define|definition|meaning|mean|means|stand|stands|"
    r"full\s*form|expand|expansion|abbreviation|acronym|kya\s+hai|ka\s+matlab|matlab)\b",
    re.IGNORECASE)
_QUESTION_FILLER = {"what", "is", "are", "does", "do", "the", "a", "an", "of", "for", "stand", "stands",
                    "mean", "means", "meaning", "define", "definition", "full", "form", "expand",
                    "expansion", "abbreviation", "acronym", "kya", "hai", "ka", "matlab", "in", "this",
                    "document", "manual", "policy", "please", "tell", "me", "whats", "what's"}


def find_abbreviation(raw_query: str) -> dict | None:
    """Rule-based (no AI needed) check for "what is X" / "full form of X" /
    "X meaning" questions, or a query that is just an abbreviation. Returns
    the glossary entry for X, or None. This is what makes "what is FAA"
    work even when the local AI model isn't installed."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9&/\-]*", raw_query)
    content = [t for t in tokens if t.lower() not in _QUESTION_FILLER]
    is_definition_question = bool(_DEFINITION_QUESTION_RE.search(raw_query)) or len(content) == 1
    if not is_definition_question or len(content) != 1:
        return None          # e.g. "who approves LTE" is not asking what LTE stands for
    for tok in content:
        entry = glossary.lookup(tok)
        if entry:
            return entry
    return None


def search(raw_query: str, file_filter: list[str] | None = None) -> dict:
    """Top-level entry point used by the UI: classifies intent, retrieves,
    logs the query to the audit trail, and returns everything the UI needs
    to render (results, detected mode, glossary bridge notices, etc.)."""
    classification = classify_query(raw_query)
    mode = classification["mode"]
    cleaned_query = classification["cleaned_query"]

    # Abbreviation questions are answered from the documents' own
    # abbreviations, with or without the AI classifier.
    glossary_answer = glossary.lookup(classification["entity"]) if classification["entity"] else None
    if glossary_answer is None:
        glossary_answer = find_abbreviation(raw_query)
        if glossary_answer:
            classification["entity"] = glossary_answer["abbr"]
            if mode == "lookup":
                mode = "definition"
    if glossary_answer and glossary_answer["abbr"] not in cleaned_query:
        cleaned_query = f"{cleaned_query} {glossary_answer['abbr']}"

    results = retrieve(cleaned_query, mode=mode, file_filter=file_filter, entity=classification["entity"])

    low_confidence = bool(results) and max(r.semantic_score for r in results) < LOW_CONFIDENCE_SIMILARITY

    log_audit({
        "low_confidence": low_confidence,
        "raw_query": raw_query,
        "cleaned_query": cleaned_query,
        "detected_mode": mode,
        "language": classification["language"],
        "fallback_used": classification["fallback_used"],
        "file_hint": classification["file_hint"],
        "entity": classification["entity"],
        "result_count": len(results),
        "sources": sorted({r.filename for r in results}),
        "citations": [r.citation for r in results],
    })

    return {
        "mode": mode,
        "cleaned_query": cleaned_query,
        "entity": classification["entity"],
        "file_hint": classification["file_hint"],
        "language": classification["language"],
        "fallback_used": classification["fallback_used"],
        "results": results,
        "low_confidence": low_confidence,
        "glossary_answer": glossary_answer,
    }
