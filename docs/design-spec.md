# Local LLM Document Preprocessing Pipeline — Design Spec

**Date:** 2026-09-18
**Owner:** Mirza (AI dev, FARO)
**Status:** Draft — pending review

## 1. Context

FARO (import/export of phone parts and accessories, multiple shops across Germany)
is deploying a local, air-gapped LLM for handling sensitive business documents
(invoices, inventory lists, sales records) in PDF, CSV, Word, and Excel formats.

**Current setup:**
- Mac Studio M2 Ultra, 64GB unified memory, LAN-only (no general internet access).
  Owned/administered by the Head of IT — the author has no shell/file access to it,
  only the Open WebUI URL over LAN.
- [Open WebUI](https://github.com/open-webui/open-webui) running as the chat frontend.
- Model served via Ollama: **Qwen3.6-27B** (dense, 27B params, 262,144-token default
  context window, extendable to 1,010,000 via YaRN; released April 2026).
- Non-technical shop employees upload arbitrary files directly into Open WebUI chat —
  there is no curated ingestion step today.
- **All documents and all users are German.** This affects OCR (Tesseract needs the
  `deu` language pack, not the English default), the model's response language
  (should default to German, matching what users type), and — a real gap noted here
  rather than retrofitted mid-build — Phase 0's synthetic test fixtures are in
  English, so they validate extraction *mechanics* but not German-language handling
  specifically. Before Phase 1 packaging, run at least one German-language synthetic
  or anonymized-real document through the pipeline.

**Author's own hardware:** Dell OptiPlex (i5/8GB) at work — cannot run or test any
model locally. Personal PC (Ryzen 5800X3D, 16GB RAM, RTX 3070) available for
development and testing with a small local model and dummy/synthetic data before
anything is handed to IT.

## 2. Problem

Uploading real documents produces wrong or inconsistent answers:
- A 41-page PDF invoice: asking "how many articles were in the document" returns a
  different, wrong number each time, or the model hallucinates.
- A 1,000+ row Excel/CSV file (messy, unsorted, noisy): performance is worse still.

**Root cause:** Open WebUI's default RAG pipeline (chunk → embed → retrieve top-K
similar chunks) discards most of a document before the model ever sees it. Counting
and aggregation questions require seeing *every* row, not the top-K semantically
similar ones — RAG is structurally the wrong tool for that question shape, regardless
of model quality. Separately, even given full context, an LLM asked to count rows in
a wall of text is unreliable — counting should never be delegated to the model.

## 3. Goals / success criteria

- Exact counts and totals over a single uploaded document (PDF or spreadsheet) are
  numerically correct, every time — not model-dependent, not RAG-dependent.
- Specific field lookups ("price of article X", "supplier on this invoice") work
  reliably against messy, real-world documents.
- Qualitative summarization continues to work as it does today (this is not broken).
- Non-technical employees keep using the existing Open WebUI upload flow — no new
  tool or step required of them.
- The solution is deployable by the Head of IT with a short, concrete install
  checklist, without requiring the author to have Mac Studio access.
- Design doesn't block a later phase adding cross-document/archive search.

## 4. Non-goals (for this phase)

- Cross-document / historical archive search (explicitly deferred to Phase 2 — see
  §8). Today's usage is single-document Q&A per upload.
- OCR quality tuning beyond "good enough for scanned invoices" — deep OCR
  accuracy work is out of scope unless real usage shows it's a blocker.
- Any change to how employees upload files (stays as-is: drag into Open WebUI chat).

## 5. Architecture

Implemented as an **Open WebUI Pipeline** (Open WebUI's native plugin framework for
intercepting/augmenting requests) — not a separate proxy service — since it installs
into infrastructure that's already running, requires no changes to how Open WebUI is
pointed at Ollama, and is exactly what the framework is designed for.

```
Employee uploads file in Open WebUI chat
            |
            v
   [Pipeline: file-type router]
    +- PDF ------------> extract text per page (PyMuPDF)
    |                     page has ~0 extractable text? --> OCR that page only (Tesseract, local)
    |                     detect table regions --> render as markdown tables
    |                     surrounding prose kept as-is (mixed header/table/footer content)
    |
    +- XLSX/CSV -------> load with pandas
    |                     auto-detect real header row (skip title/logo rows)
    |                     drop fully-empty rows/cols, normalize types
    |                     render as clean markdown table
    |                     compute FACTS block: row_count, per-numeric-column
    |                                          sum/min/max/avg, unique counts
    |                     keep parsed DataFrame in memory for tool-calling (see 6)
    |
    +- DOCX/other -----> extract text (python-docx or similar)
    |
    +- unrecognized ---> generic text extraction attempt; if unreadable, tell the
                          user explicitly ("couldn't fully parse this file") instead
                          of silently guessing
            |
            v
   [Assembled context for the model]
    = cleaned document text/tables + FACTS block + original user question
            |
            v
   Sent to Qwen3.6-27B as full context (chunking/embedding RAG bypassed for
   single-document mode — the 262K context window comfortably fits these
   document sizes as clean text)
```

## 6. Exact counts/sums: tool-calling, not text-reading

A FACTS block in context is a large improvement over the current state, but still
relies on the model correctly reading a number out of text, which is not bulletproof
for compound questions (e.g. "how many articles above €50?"). The more robust
mechanism: expose the parsed table as **callable tools**, backed by the actual
in-memory pandas DataFrame for that upload —

- `count_rows(filter?)`
- `sum_column(name, filter?)`
- `get_row(id)`

Qwen3.6-27B supports tool/function calling via Ollama. The model calls the tool and
reports the exact value computed by code — the same pattern as a text-to-SQL agent,
scoped to one in-memory table per uploaded file. This directly resolves the
"how many articles were in the document" failure mode: it becomes `len(df)` (with
any filter implied by the question), not a guess.

## 7. Resource planning (for IT)

The Mac Studio already runs Qwen3.6-27B via Ollama. Three considerations to raise with
the Head of IT before deployment:

- **German OCR language pack.** All documents and users are German. The OCR fallback
  (Tesseract) must have the `deu` trained-data language pack installed, not just the
  English default — without it, scanned German documents (ä/ö/ü/ß) will OCR poorly
  or garble. The pipeline should call Tesseract with `lang='deu+eng'` (mixed, since
  branding/company names may be Latin-script English), and IT needs the `deu`
  language pack present on the Mac Studio alongside the `eng` one already implied by
  a default Tesseract install.

- **KV cache cost of large contexts.** 262K tokens of context on a 27B model requires
  substantial memory beyond the model weights. Recommend a **capped context budget
  (32K–64K tokens)** to start — comfortably covers the stated document sizes
  (41-page PDF, 1,000-row sheet) without risking OOM or slowdowns. Raise the cap only
  if real usage demonstrates the need.
- **The Pipeline runs as a small additional Python process** alongside Ollama/Open
  WebUI — CPU-only (pandas/PyMuPDF/Tesseract, no GPU dependency), but still shares
  the box's 64GB unified memory, so it should be kept lean (no heavyweight embedding
  or vision models in this phase).

## 8. Phased rollout

**Phase 0 — Build & test on personal hardware (author, no Mac Studio access needed).**
Pull a small Ollama model locally (model size doesn't matter for validating
extraction/counting correctness) on the Ryzen/RTX 3070 PC. Use synthetic or
Kaggle-sourced invoice/inventory data shaped like FARO's real documents (phone parts
line items, supplier invoices). Validate the full flow end-to-end: upload → extract
→ FACTS → tool-calling → answer.

**Phase 1 — Package for IT.**
Ship as an Open WebUI Pipelines-compatible Python module (or Docker image) with a
pinned `requirements.txt` — no internet dependency resolution needed at install time,
since the Mac Studio has no general internet access. Deliverable to the Head of IT:
- One file/image + pinned dependencies.
- A short checklist: (a) confirm Open WebUI's Pipelines feature is enabled, (b) drop
  in the package, (c) restart the Pipelines service, (d) confirm whether Docker or
  bare Python is available on the Mac Studio so packaging matches.

**Phase 2 — IT deploys; validate against real documents over LAN.**
Author tests via the normal Open WebUI LAN access, iterates based on real (or
anonymized real) FARO documents.

**Phase 3 — Cross-document/archive search (deferred, not blocking this phase).**
Add a lightweight local vector DB (e.g. Chroma — pure Python, no external service
dependency) indexed off the same FACTS + cleaned-text output this pipeline already
produces. Additive on top of Phase 1–2 output, not a redesign.

## 9. Open questions / risks

- Exact available install mechanism on the Mac Studio (Docker vs. bare Python) is
  unconfirmed — Phase 1 packaging should be finalized once the Head of IT confirms.
- The Mac Studio's actual internet access is inconsistent with "LAN only" as stated
  (Ollama model pulls from the registry require some outbound access) — worth
  clarifying with IT exactly what is and isn't reachable, since it affects whether
  pinned-wheel sneakernet transfer is strictly required or partial online install is
  possible.
- OCR quality on scanned documents is unvalidated — flagged as a risk to watch in
  Phase 2, not solved upfront (see Non-goals).
- Real-world document variety (beyond PDF/XLSX/CSV/DOCX) hasn't been surveyed — the
  generic-fallback + explicit "couldn't parse" behavior is the mitigation until more
  real samples are seen.

## 10. Out of scope for this spec

- SEO work / competitor analysis (unrelated request noted separately, not part of
  this project).
- Frontend design tooling evaluation (unrelated request noted separately).
