"""
app.py — Streamlit UI: drag-drop file library, search bar, result cards,
abbreviation box, glossary conflict warnings, and on-demand AI
explanations (local Llama via Ollama, clearly labelled, never replacing
the verbatim clause text). Library operations live in library.py.
"""

import config  # noqa: F401 — imported first so offline flags are set

import json
import os
import threading

import requests
import streamlit as st

import answer
import explain
import glossary
import ingest
import library
import search

FILE_TYPE_BADGES = {
    "pdf": "PDF",
    "scanned_pdf": "Scanned PDF",
    "image": "Image",
    "docx": "Word",
}

load_manifest = library.load_manifest
remove_file = library.remove_file


def ollama_server_up() -> bool:
    try:
        requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def download_ai_model() -> None:
    """One-time download of the local AI model, done by the local Ollama
    server (the only time anything uses the internet, and only when the
    user clicks the button). This app process itself stays network-locked:
    it only talks to Ollama on 127.0.0.1."""
    bar = st.progress(0.0, text="Starting download...")
    try:
        with requests.post(f"{config.OLLAMA_BASE_URL}/api/pull",
                           json={"model": config.OLLAMA_MODEL, "stream": True}, stream=True, timeout=None) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    st.error(f"Download failed: {msg['error']}. Check the internet connection and try again.")
                    return
                total, done = msg.get("total"), msg.get("completed")
                if total and done:
                    bar.progress(min(done / total, 1.0), text=f"Downloading: {done >> 20} of {total >> 20} MB")
        st.success("Local AI is ready. You can disconnect from the internet.")
        ollama_status.clear()
    except Exception as e:
        st.error(f"Download failed ({e}). Check the internet connection and try again.")


@st.cache_data(ttl=15, show_spinner=False)
def ollama_status() -> bool:
    """True if the local Ollama server answers and has the configured model."""
    try:
        r = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=2)
        names = {m.get("name", "").split(":")[0] for m in r.json().get("models", [])}
        return config.OLLAMA_MODEL.split(":")[0] in names
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Policy Lookup", page_icon="📘", layout="wide")

if "file_filter" not in st.session_state:
    st.session_state.file_filter = None  # None = search all files
if "mode_override" not in st.session_state:
    st.session_state.mode_override = None
if "last_query" not in st.session_state:
    st.session_state.last_query = ""
if "last_result" not in st.session_state:
    st.session_state.last_result = None


# ---------------------------------------------------------------------------
# Sidebar — document library
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("📚 Document Library")

    uploaded_files = st.file_uploader(
        "Drop files here or click to upload",
        type=["pdf", "docx", "jpg", "jpeg", "png", "tiff", "tif", "bmp"],
        accept_multiple_files=True,
        label_visibility="visible",
    )
    if uploaded_files:
        manifest_names = {e["filename"] for e in load_manifest()}
        new_files = [f for f in uploaded_files if f.name not in manifest_names]
        for f in new_files:
            with st.spinner(f"Adding {f.name}..."):
                result, conflicts = library.add_uploaded_bytes(f.name, f.getvalue())
            st.success(f"Added {f.name} — {result.page_count} pages, {len(result.chunks)} clauses.")
            if result.used_ocr:
                engine = ingest.ocr_engine()
                extra = (" The basic OCR engine was used (the bundled Tesseract isn't available on this "
                         "computer), so some lines may be missing." if engine == "rapidocr" else "")
                st.warning("⚠ Scanned pages were read with OCR — accuracy depends on scan quality. "
                           "Verify results against the original." + extra)
            for c in conflicts:
                st.warning(
                    f"⚠ '{c['abbreviation']}' defined differently across files — "
                    f"using definition from {c['used_source']}. ({c['conflicting_source']} disagrees.)"
                )
        if new_files:
            st.rerun()

    st.divider()

    manifest = load_manifest()
    if not manifest:
        st.caption("No documents added yet.")
    else:
        st.subheader("Filter")
        all_selected = st.checkbox("☑ All files", value=st.session_state.file_filter is None)
        if all_selected:
            st.session_state.file_filter = None

        selected_files = []
        for entry in manifest:
            badge = FILE_TYPE_BADGES.get(entry["file_type"], entry["file_type"])
            cols = st.columns([5, 1])
            with cols[0]:
                checked = st.checkbox(
                    f"📄 {entry['filename']}",
                    value=True if all_selected else (entry["filename"] in (st.session_state.file_filter or [])),
                    key=f"filter_{entry['filename']}",
                    disabled=all_selected,
                )
                st.caption(
                    f"[{badge}] {entry['page_count']} pages · {entry['clause_count']} clauses · "
                    f"Added {entry['date_added']}"
                )
            with cols[1]:
                if st.button("✕", key=f"remove_{entry['filename']}", help="Remove this file"):
                    remove_file(entry["filename"])
                    st.rerun()
            if checked or all_selected:
                selected_files.append(entry["filename"])

        if not all_selected:
            st.session_state.file_filter = selected_files if selected_files else None

        st.divider()
        total_clauses = sum(e["clause_count"] for e in manifest)
        glossary_count = len(glossary.load_glossary())
        st.caption(f"**Total:** {total_clauses} clauses · {glossary_count} abbreviations")

        conflicts = glossary.get_conflicts()
        if conflicts:
            with st.expander(f"⚠ {len(conflicts)} abbreviation(s) defined differently across files"):
                for abbr, entries_ in conflicts.items():
                    st.write(f"**{abbr}**")
                    for c in entries_:
                        st.caption(f"Using: \"{c['used_definition']}\" ({c['used_source']})")
                        st.caption(f"Also found: \"{c['conflicting_definition']}\" ({c['conflicting_source']})")

    st.divider()
    st.subheader("🤖 Local AI")
    if ollama_status():
        st.caption(f"✅ {config.OLLAMA_MODEL} running offline — short answers and "
                   "plain-language explanations are on.")
    elif ollama_server_up():
        st.caption("The local AI model isn't installed yet. Search already works without it. "
                   "One-time download, about 2 GB — needs internet once; your documents are never sent.")
        if st.button("⬇ Download local AI model"):
            download_ai_model()
    else:
        st.caption(f"⚪ Local AI not running — search still works; short AI answers are off.")

    st.divider()
    if st.button("⏻ Quit Policy Lookup", help="Stops the app and the local AI"):
        st.info("Policy Lookup has stopped. You can close this window.")
        threading.Timer(1.0, lambda: os._exit(0)).start()


# ---------------------------------------------------------------------------
# Main — search
# ---------------------------------------------------------------------------

st.title("📘 Policy Lookup")

if not manifest:
    st.info("👈 Add a document from the sidebar to get started.")
    st.stop()

query = st.text_input(
    "Search",
    placeholder="Ask anything — clause, keyword, abbreviation, question in any language",
    label_visibility="collapsed",
    key="search_box",
)

MODE_LABELS = {
    "definition": "📖 Definition mode",
    "enumeration": "📋 Enumeration mode",
    "sequence": "🔢 Sequence mode",
    "permission": "✅ Permission mode",
    "lookup": "🔍 Lookup mode",
}


def run_search(q: str):
    file_filter = st.session_state.file_filter
    result = search.search(q, file_filter=file_filter)
    if st.session_state.mode_override:
        result["mode"] = st.session_state.mode_override
        result["results"] = search.retrieve(
            result["cleaned_query"], mode=st.session_state.mode_override,
            file_filter=file_filter, entity=result["entity"],
        )
    st.session_state.last_result = result
    st.session_state.last_query = q


if query and query != st.session_state.last_query:
    st.session_state.mode_override = None
    run_search(query)
elif query and st.session_state.mode_override and st.session_state.last_result:
    run_search(query)

result = st.session_state.last_result

if result:
    mode = result["mode"]
    cleaned = result["cleaned_query"]
    raw_query = st.session_state.last_query

    # --- status line ---
    status_bits = [MODE_LABELS.get(mode, mode)]
    if st.session_state.file_filter:
        names = ", ".join(st.session_state.file_filter)
        status_bits.append(f"🔍 Searching: {names}")
    else:
        status_bits.append("🔍 Searching: All files")
    st.caption(" · ".join(status_bits))

    if cleaned.strip().lower() != raw_query.strip().lower():
        st.caption(f"Interpreted as: _{cleaned}_")

    override_cols = st.columns(6)
    override_cols[0].caption("Wrong mode? Switch to:")
    for i, m in enumerate(["definition", "enumeration", "sequence", "permission", "lookup"]):
        if m != mode and override_cols[i + 1].button(m.capitalize(), key=f"override_{m}"):
            st.session_state.mode_override = m
            run_search(raw_query)
            st.rerun()

    # --- abbreviation box ---
    entity = result.get("entity")
    glossary_map = glossary.load_glossary()
    if entity and entity in glossary_map:
        st.success(f"**{entity}** = {glossary_map[entity]}")

    # --- glossary bridge notice ---
    _, bridge_matches = search.glossary_bridge_expand(cleaned)
    for abbr, full_term in bridge_matches:
        if abbr != entity:
            st.caption(f"🔁 Glossary: matched {abbr} → also searched {full_term}")

    st.divider()

    results = result["results"]
    if not results:
        st.warning("No results found. Try rephrasing your question, or check the spelling of any abbreviation.")
    elif result.get("low_confidence"):
        st.warning("No clause closely matches this question — these are only the nearest matches. "
                   "The answer may not be in these documents; check carefully before relying on them.")
    else:
        # --- short answer first, exact text right under it ---
        st.warning("⚠️ **Offline AI tool — it can be wrong.** Use the short answer to orient yourself, "
                   "then read the exact text from the document before you sign or act on it.")
        ans_key = f"answer::{raw_query}::{mode}::{st.session_state.file_filter}"
        if ans_key not in st.session_state:
            with st.spinner("Finding the short answer (on this computer, offline)..."):
                ai = answer.ai_answer(raw_query, results) if (ollama_status() and not result.get("low_confidence")) else None
                sentence, src = answer.key_sentence(raw_query, results)
                st.session_state[ans_key] = (ai, sentence, src)
        ai, sentence, src = st.session_state[ans_key]

        def _source_line(r) -> str:
            return f"📍 {r.filename} · **{r.citation}** · page {r.page_number}"

        def _quote(text: str) -> None:
            st.markdown("> " + text.replace("\n", " "))

        with st.container(border=True):
            if ai and not ai.get("not_answered"):
                st.markdown(f"**Short answer** 🤖  \n{ai['answer']}")
                st.caption(_source_line(ai["result"]) + " — exact words from the document:")
                _quote(ai["quote"])
            else:
                if result.get("low_confidence"):
                    st.markdown("**No clause closely matches this question.** The nearest text is:")
                else:
                    st.markdown("**Most relevant line** (exact text from the document):")
                if sentence:
                    _quote(sentence)
                    st.caption(_source_line(src))
                if not ollama_status():
                    st.caption("Local AI is off, so there is no AI summary — this line is picked by search, not written by AI.")
                elif not result.get("low_confidence"):
                    st.caption("No AI summary: the local AI's answer couldn't be verified word-for-word against "
                               "the document, so only the exact text is shown.")

        st.markdown("##### Matching clauses — exact text")
        expand_by_default = mode in ("enumeration", "sequence", "permission")

        # group by chapter when 3+ results share one
        chapter_counts: dict[str, int] = {}
        for r in results:
            if r.chapter:
                chapter_counts[r.chapter] = chapter_counts.get(r.chapter, 0) + 1
        group_chapters = {c for c, n in chapter_counts.items() if n >= 3}

        current_chapter_header = None
        for i, r in enumerate(results, start=1):
            if r.chapter in group_chapters and r.chapter != current_chapter_header:
                current_chapter_header = r.chapter
                st.markdown(f"#### {current_chapter_header}")

            with st.container(border=True):
                badge_bits = [f"[{r.filename}]", f"[{r.match_type}]"]
                st.markdown(f"**{i}. {r.citation} · page {r.page_number}**")
                st.caption(" ".join(badge_bits))
                preview = r.text[:220] + ("..." if len(r.text) > 220 else "")
                if not expand_by_default:
                    st.markdown(f"*{preview}*")
                if expand_by_default:
                    st.markdown(
                        f"<div style='background:rgba(127,127,127,0.08); padding:0.75em; "
                        f"border-radius:0.4em;'>{r.text}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    with st.expander("Show full text"):
                        st.write(r.text)

                exp_key = f"explain::{r.filename}::{r.chunk_index}"
                if exp_key in st.session_state:
                    if st.session_state[exp_key]:
                        st.info(f"🤖 **AI explanation (plain language — check against the clause text above):** "
                                f"{st.session_state[exp_key]}")
                    else:
                        st.caption("The local AI didn't respond — showing the verbatim clause only.")
                elif ollama_status() and st.button("🤖 Explain in plain language", key=f"btn_{exp_key}"):
                    with st.spinner("Asking the local AI (runs on this computer, offline)..."):
                        st.session_state[exp_key] = explain.explain_clause(r.citation, r.text)
                    st.rerun()
