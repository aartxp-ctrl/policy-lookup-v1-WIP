# Policy Lookup — offline search for rule books and manuals

Ask a question in plain English (or Hinglish) and get the **exact clause**
from your documents, with its **citation and page number**: e.g.
*"termination of service on account of misconduct"* → *Army Rules,
Chapter III · 14(1), page 17*.

Runs **entirely offline** on a Mac (Apple Silicon) or Windows PC (64-bit
Intel/AMD), straight from an external drive. Nothing is installed on the
computer and nothing leaves it.

## How it works

```
PDF / scan / Word ──► text (native text layer, or OCR for scanned pages)
                  ──► clauses split on the document's own numbering
                      (14 → 14(1) → 14(1)(a); 4.6.1(a); Problem Statement 132 · 4)
                  ──► local embeddings (all-MiniLM-L6-v2) in a local ChromaDB index
question ─────────► hybrid search: semantic + keyword (typo-tolerant,
                    abbreviation-aware via an auto-built glossary)
                  ──► verbatim clauses with citations
optional ─────────► local Llama 3.2 (Ollama): understands the question's intent,
                    and explains a clause in plain language on request
```

Design choices:
- **Your documents never leave the computer.** Files are uploaded into the app running on
  this machine (127.0.0.1 — not even other computers on the network can open it). The app
  process has a built-in **network lock**: any connection or DNS lookup to anything other than
  this computer is refused before a byte is sent, proxies are ignored, and telemetry is off.
  The AI model (Llama 3.2 via Ollama), search model and OCR all run locally. Works with
  Wi-Fi switched off.
- **Any PDF, scan or Word file.** Numbered rule books (14(1)(a), 4.6.1), section-style
  policies ("Section 2: Annual Leave"), plain unnumbered documents (split into paragraphs)
  and scanned pages (Tesseract 5 OCR bundled with the app; a basic fallback OCR if it
  can't run on an older Mac).
- **Answers are the document's own words.** The AI never decides which clause
  is relevant and never rewrites the clause; its explanations are labelled and
  shown *under* the verbatim text.
- **Every search is logged** (`data/audit_log.jsonl`) with the citations returned.
- **Short answer first, exact text right under it.** The local AI writes a 1–2 sentence answer and must quote the clause it used; the quote is checked word-for-word against the document, and every number or name in the answer must appear in that clause. If any check fails, no AI answer is shown — only the most relevant exact sentence. A warning reminds the reader to check the text before signing.
- **Weak matches are flagged.** If nothing in the library is really about the
  question, the app says so instead of presenting the nearest clauses as answers.
- **Works without the AI.** If Llama isn't running, search still works.

## Install

**[Installation guide — Windows and Mac](INSTALL.md)**: download the installer from
Releases, then *Next → Install → Finish* (Windows) or drag to Applications (Mac).

Running from a prepared USB/external drive instead (fully offline, no install):
double-click `Run.bat` (Windows) or `Run.command` (Mac) — see
[docs_drive_and_windows_details.md](docs_drive_and_windows_details.md).

## What's in this folder

| Path | What |
|---|---|
| `app.py` | Streamlit interface |
| `ingest.py` | Text extraction, OCR, clause splitting |
| `search.py` | Hybrid retrieval, intent classification, audit log |
| `glossary.py` | Builds the abbreviation glossary from the documents |
| `answer.py` | Short answer on top: AI answer checked word-for-word against its quoted source, or the most relevant exact sentence |
| `explain.py` | Plain-language explanation of one clause (local Llama) |
| `library.py`, `ingest_cli.py` | Document library; bulk ingest from a folder |
| `config.py` | All settings (offline flags, paths, thresholds) |
| `launch.py`, `Run.command`, `Run.bat` | One-click offline launcher |
| `models/` | Embedding model (local) |
| `runtime/` | Bundled Python, Ollama + Llama 3.2, offline package wheels (Mac + Windows) |
| `data/` | Your index, glossary and audit log (created on first use) |

## Tested on the three reference documents

DPM 2025 Vol I (295 pages), Army Rules 1954 (214 pages) and the ADB
Compendium of Problem Definition Statements Vol 4 (60 pages, 17 scanned) →
3,245 clauses, indexed in about a minute. 10 of 10 test questions returned
the right clause in the top 3 with the correct page.

## Known limitations

- Scanned pages depend on scan quality — verify OCR'd results against the original.
- Tables are read as text rows; a table-heavy page can produce weak clauses.
- Runtimes are for Apple Silicon Macs and x64 Windows only.
- The drive must be **exFAT** to be readable on both Mac and Windows.

## Building from source (developers)

1. Download the code (Code → Download ZIP) and extract it.
2. Double-click **`Setup.bat`** (Windows) or **`Setup.command`** (Mac). It downloads Python,
   the packages, Ollama, Llama 3.2 and the embedding model into this folder (~3–4 GB).
3. From then on, **`Run.bat`** / **`Run.command`** work fully offline.

Step-by-step Windows guide: [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md).

Developers: `pip install -r requirements.txt`, `python setup_models.py`, `ollama pull llama3.2`,
`streamlit run app.py`.
