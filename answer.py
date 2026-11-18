"""
answer.py — the short answer shown above the search results.

Two layers, so a reader always gets something short to read first and the
exact source text to check it against:

1. key_sentence(): no AI. Picks the single sentence, from the best-matching
   clauses, that most directly addresses the question, and returns it
   VERBATIM with its citation. Always available, fully deterministic.

2. ai_answer(): local Llama (Ollama) writes a 1-2 sentence answer from the
   top clauses only, naming its source clause and quoting the exact phrase
   it relied on. The quote is then checked against the clause text: if it
   isn't found word-for-word, the AI answer is discarded and only the
   verbatim key sentence is shown. Never raises; returns None on any failure.
"""

import config  # noqa: F401 — imported first so offline flags are set

import json
import re

import requests

# Sentence ends, plus bullet glyphs (and the "?" a missing bullet glyph often
# turns into after OCR/PDF extraction) — the glyph itself is dropped, so each
# piece is still an exact substring of the document.
_SENTENCE_SPLIT = re.compile(r"\s+[•▪◦‣●■?]\s+(?=[A-Z])|(?<=[.;:?])\s+(?=[\"“(A-Z0-9])")
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", text).strip().lower()


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    out = []
    for p in parts:
        words = p.split()
        # very long run-on "sentences" (tables, lists) are cut into short,
        # overlapping windows so one table row can be picked on its own
        if len(words) > 50:
            for start in range(0, len(words), 20):
                out.append(" ".join(words[start:start + 30]))
                if start + 30 >= len(words):
                    break
        elif words:
            out.append(" ".join(words))
    return out


_QUANTITY_Q = re.compile(r"\b(how much|how many|how long|percent|percentage|maximum|minimum|max|min|limit|"
                         r"age|amount|rate|value|period|days|years|time limit|ceiling|threshold|penalty|fine|fee|charge|"
                         r"damages|cost|price|deadline|within)\b", re.I)
_HAS_QUANTITY = re.compile(r"\d|₹|\bper ?cent\b|%")
_CONDITION_Q = re.compile(r"\b(when|whether|can|may|is it|allowed|permitted|required|mandatory)\b", re.I)
_HAS_CONDITION = re.compile(r"\b(when|where|if|provided|unless|only|shall not|may|in cases?)\b", re.I)


def key_sentence(question: str, results: list, top_n: int = 3):
    """Returns (sentence, result) — the most relevant verbatim sentence among
    the top results — or (None, None) if there are no results."""
    if not results:
        return None, None
    import numpy as np

    model = config.get_embedding_model()
    from search import _crude_stem

    q_tokens = {_crude_stem(t) for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 2}
    # distinctive words in the question — ranks, abbreviations, proper nouns
    q_specific = {t.lower() for t in re.findall(r"\b[A-Z][A-Za-z0-9]*\b", question)
                  if t.lower() not in {"what", "when", "who", "how", "can", "is", "are", "does", "the", "which", "where", "why"}}
    wants_quantity = bool(_QUANTITY_Q.search(question))
    wants_condition = bool(_CONDITION_Q.search(question))
    candidates = []
    for rank, r in enumerate(results[:top_n]):
        sents = split_sentences(r.text)
        flat = " ".join(r.text.split())
        for i, s in enumerate(sents):
            if len(s.split()) >= 4:
                candidates.append((rank, s, r))
            # "Fraudulent Practice. Any omission or ..." — a short term/heading
            # followed by its definition is offered as one candidate.
            if len(s.split()) <= 5 and i + 1 < len(sents) and f"{s} {sents[i + 1]}" in flat:
                candidates.append((rank, f"{s} {sents[i + 1]}", r))
    if not candidates:
        return results[0].text, results[0]
    vecs = model.encode([question] + [c[1] for c in candidates], normalize_embeddings=True)
    sims = vecs[1:] @ vecs[0]
    best, best_score = None, -1.0
    for (rank, s, r), sim in zip(candidates, sims):
        s_raw = set(re.findall(r"[a-z0-9]+", s.lower()))
        s_tokens = s_raw | {_crude_stem(t) for t in s_raw}
        overlap = len(q_tokens & s_tokens) / max(1, len(q_tokens))
        score = float(sim) + 0.25 * overlap - 0.04 * rank   # favour the top-ranked clause slightly
        score += min(0.3, 0.15 * len(q_specific & s_raw))
        if wants_quantity and _HAS_QUANTITY.search(s):
            score += 0.12
        if wants_condition and _HAS_CONDITION.search(s):
            score += 0.1 if re.match(r"\s*when\b", question, re.I) else 0.05
        if len(s.split()) < 8:
            score -= 0.15        # a bare heading answers nothing
        if score > best_score:
            best, best_score = (s, r), score
    return best


ANSWER_SYSTEM_PROMPT = """You answer questions from an officer using ONLY the numbered clauses provided.
Return ONLY JSON: {"answer": "...", "source": <clause number 1-3, or 0>, "quote": "..."}
Rules:
- "answer": at most 2 short sentences, plain English, directly answering the question
  (yes/no first if it is a yes/no question). Include key numbers, limits or conditions.
- Keep the clause's qualifiers exactly: words like "normally", "only", "except", "subject to
  approval of", "in exceptional cases". Never turn an exception into a plain "yes".
- If the rule applies only to certain people, cases or amounts, say so (e.g. "for officers of
  Armoured Corps, Infantry ...").
- "source": which clause the answer comes from (1, 2 or 3).
- "quote": copy the exact words (8-40 words) from that clause that support the answer.
  Copy character for character. Do not paraphrase or join separate parts.
- If the clauses do not answer the question, return
  {"answer": "The closest clauses below do not directly answer this.", "source": 0, "quote": ""}
- Never use outside knowledge. Never invent numbers."""


def ai_answer(question: str, results: list, top_n: int = 3) -> dict | None:
    """{"answer", "result", "quote", "verified": True} or a not-answered dict,
    or None if the local AI is unavailable or its quote can't be verified."""
    top = results[:top_n]
    if not top:
        return None
    clauses = "\n\n".join(
        f"[{i}] {r.citation} (page {r.page_number}, {r.filename}):\n{r.text[:1500]}"
        for i, r in enumerate(top, start=1)
    )
    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "system": ANSWER_SYSTEM_PROMPT,
                "prompt": f"Question: {question}\n\nClauses:\n{clauses}",
                "stream": False,
                "format": "json",
                "options": {"temperature": 0, "num_ctx": 4096},
            },
            timeout=config.EXPLAIN_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = json.loads(resp.json().get("response", ""))
    except Exception:
        return None

    answer = (data.get("answer") or "").strip()
    try:
        source = int(data.get("source") or 0)
    except (TypeError, ValueError):
        source = 0
    quote = (data.get("quote") or "").strip().strip('"“”')
    if not answer:
        return None
    if source == 0:
        return {"answer": answer, "result": None, "quote": "", "verified": True, "not_answered": True}
    if not (1 <= source <= len(top)) or len(quote.split()) < 4:
        return None
    result = top[source - 1]
    verified = _locate_quote(quote, result.text)
    if verified is None or not _answer_supported(answer, result.text, question) or not _answer_matches_quote(answer, verified):
        # The model's "quote" isn't actually in the clause — don't show an
        # answer whose support we can't point to word-for-word.
        return None
    return {"answer": answer, "result": result, "quote": verified, "verified": True, "not_answered": False}


def _locate_quote(quote: str, clause_text: str) -> str | None:
    """Checks the model's quote against the clause and returns the exact
    supporting text FROM THE CLAUSE (the full sentence(s) containing it), or
    None if any part of the quote isn't in the clause word-for-word. Models
    often trim a quote with "..."; each trimmed fragment is checked in order."""
    fragments = [f for f in re.split(r"\s*(?:\.\.\.|…)\s*", quote) if len(f.split()) >= 3]
    if not fragments:
        return None
    clause_norm = _norm(clause_text)
    pos = 0
    for frag in fragments:
        idx = clause_norm.find(_norm(frag).rstrip(".;:,"), pos)
        if idx < 0:
            return None
        pos = idx + 1
    first = _norm(fragments[0]).rstrip(".;:,")
    last = _norm(fragments[-1]).rstrip(".;:,")
    sentences = [x.strip() for x in _SENTENCE_SPLIT.split(clause_text) if x.strip()]
    start = next((i for i, sen in enumerate(sentences) if first[:60] in _norm(sen)), None)
    end = next((i for i, sen in enumerate(sentences) if last[-60:] in _norm(sen)), None)
    if start is None:
        return fragments[0]
    if end is None or end < start:
        end = start
    text = " ".join(sentences[start:end + 1][:3])
    words = text.split()
    return text if len(words) <= 90 else " ".join(words[:90]) + " …"


_NUMBER_IN_ANSWER = re.compile(r"₹?\s?\d[\d,]*(?:\.\d+)?\s?%?")
_NAME_IN_ANSWER = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]+(?:\s+(?:of|and|for|the|&)?\s*[A-Z][a-z]+)+)")


ANSWER_QUOTE_MIN_SIMILARITY = 0.5   # measured: supported answers 0.58-0.85, unsupported 0.19-0.40


def _answer_matches_quote(answer: str, quote: str) -> bool:
    """The answer must say what its quote says (embedding similarity). Catches
    a real quote attached to an answer it doesn't actually support."""
    model = config.get_embedding_model()
    v = model.encode([answer, quote], normalize_embeddings=True)
    return float(v[0] @ v[1]) >= ANSWER_QUOTE_MIN_SIMILARITY


def _answer_supported(answer: str, clause_text: str, question: str = "") -> bool:
    """Every number and every multi-word proper name in the AI's answer must
    literally appear in the clause it cites. Catches the typical small-model
    failure: a fluent answer with a figure or an organisation name that isn't
    in the source at all."""
    # Words the user typed may be echoed back ("Google Drive", "5 days").
    clause_text = clause_text + " " + question
    clause_norm = _norm(clause_text).replace(",", "")
    for num in _NUMBER_IN_ANSWER.findall(answer):
        digits = re.sub(r"[^\d.]", "", num).strip(".")
        if digits and digits not in clause_norm:
            return False
    for name in _NAME_IN_ANSWER.findall(answer):
        if _norm(name) not in _norm(clause_text):
            return False
    return True
