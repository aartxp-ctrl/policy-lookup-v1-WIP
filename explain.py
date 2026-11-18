"""
explain.py — Ollama explanation layer.

Called from app.py on demand (an "Explain" button on each result). The
explanation is always rendered underneath the verbatim text and clearly
labelled as AI-generated — never mixed into the verbatim primary result.
"""

import config  # noqa: F401 — imported first so offline flags are set

import requests

EXPLAIN_SYSTEM_PROMPT = """You are explaining one clause from a compliance/procurement manual to a
non-technical reader. You will be given the clause's citation and its
exact verbatim text. Write a short, plain-language explanation of what it
means in practice — 2-4 sentences, no jargon, no legal phrasing.

Rules:
- Explain only what the given text says. Do not add information, examples,
  or assumptions not present in the text.
- Never contradict or soften the verbatim text.
- Do not repeat the verbatim text back — explain it in different, simpler
  words.
- If the text is already simple enough that no explanation would add
  value, say so briefly instead of padding.

Return plain text only — no markdown, no headings."""


def explain_clause(citation: str, verbatim_text: str) -> str | None:
    """Returns a short plain-language explanation of one clause, or None on
    any failure (unreachable Ollama, timeout, empty response) — the caller
    should simply not show an explanation section rather than surface an
    error, matching the rest of this app's silent-fallback philosophy."""
    prompt = f"Citation: {citation}\n\nClause text:\n{verbatim_text}"
    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "system": EXPLAIN_SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=config.EXPLAIN_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        return text or None
    except Exception:
        return None
