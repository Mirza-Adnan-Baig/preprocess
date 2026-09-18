# local-llm-pipeline

**New here? Read [START_HERE.md](START_HERE.md) first** — it explains the
whole project from scratch, what happened overnight, and how to set this
up on a fresh machine. This README is the shorter technical reference.

Prototype document-preprocessing pipeline for the FARO local-LLM project.
Runs entirely on local hardware with synthetic test data — no dependency on
the Mac Studio or real company documents. See
[docs/design-spec.md](docs/design-spec.md) for the full design and
[docs/phase0-plan.md](docs/phase0-plan.md) for the implementation plan this
was built from.

**Status: Phase 0 complete.** All 6 planned tasks, one cross-task fix (PDF
tables now produce a DataFrame, not just Task 4's original tabular-only
path), and a final-review fix wave are done, reviewed, and committed. Full
test suite: 31 passing, 0 skipped. The end-to-end agent test has been run
for real against a local Ollama model (`qwen2.5:7b`) on this machine, not
just unit-tested in isolation — tool-calling for exact counts is confirmed
working end to end.

## Setup

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

Install the Tesseract OCR binary separately (required for scanned-PDF
fallback): https://github.com/tesseract-ocr/tesseract — **you must also
install the German (`deu`) language pack**, not just the English default,
since all real documents are German
(`pdf.py`'s OCR call passes `lang="deu+eng"`; it will raise `TesseractError`
if `deu.traineddata` isn't installed).

To run the end-to-end agent test for real (not skipped), install
[Ollama](https://ollama.com) and pull a small tool-calling-capable model,
e.g. `ollama pull qwen2.5:7b` (matches `tests/test_agent_e2e.py`'s default).

## Test

    pytest -v

## Try it on a real document

`scripts/ask.py` is a small CLI for manually testing against a real file —
no test-writing required. Run it as a module from the repo root (plain
`python scripts/ask.py` won't find the `src` package):

    python -m scripts.ask path/to/invoice.pdf "How many articles are on this invoice?"
    python -m scripts.ask path/to/inventory.xlsx "What's the total quantity above 50 EUR?"

It prints what got extracted (kind, facts, the DataFrame if one was built)
and then the model's answer, so you can see exactly what the model was
given before trusting its response.

### Testing on a weaker PC (e.g. the office machine) against the real model

You don't need a GPU or 16GB+ RAM to run this script — extraction (pandas,
PyMuPDF) is lightweight and CPU-only. The only heavy part is the LLM call
itself, and that doesn't have to run locally: point the `ollama` client at
the Mac Studio's Ollama server over the LAN instead of running a model on
the weak PC.

1. Copy this repo to the office PC (no git remote is configured yet, so use
   a USB drive or a network share for now).
2. `pip install -r requirements.txt` (skip installing Ollama itself, and
   skip pulling a model — you're borrowing the Mac Studio's).
3. Confirm with IT whether Ollama's HTTP API on the Mac Studio is reachable
   from other machines on the LAN (Open WebUI reaching it doesn't prove
   this if Open WebUI runs on the same machine and talks to it over
   `localhost` — that's a separate question worth asking, and may need
   `OLLAMA_HOST=0.0.0.0` set on the Mac Studio's Ollama service plus a
   firewall allowance for port 11434).
4. If reachable, set the environment variable before running the script so
   it talks to that server instead of `localhost`:

       set OLLAMA_HOST=http://<mac-studio-lan-ip>:11434
       python -m scripts.ask path\to\real_invoice.pdf "How many articles are in this document?"

   and pass `--model qwen3.6:27b` (or whatever the Mac Studio's model is
   actually tagged as in `ollama list` there) instead of the default
   `qwen2.5:7b`.
5. If it's not reachable (IT hasn't opened it up, or Open WebUI is the only
   exposed interface), you can still test everything **except the final
   LLM answer** on the weak PC: extraction, the FACTS block, and the
   DataFrame the tools would operate on are all visible in the "Extraction
   summary" the script prints before it ever calls the model. That alone
   validates the harder, more failure-prone half of the pipeline (parsing a
   real messy German invoice/inventory file correctly) without needing any
   model access at all.

## Known limitations (Phase 0 — read before Phase 1 packaging)

- **PDF tables that don't repeat their header on continuation pages won't
  merge across pages.** The multi-page-table fix (`_build_pdf_dataframe` in
  `src/extractors/router.py`) merges table fragments by matching an
  identical header row — this works for the synthetic fixtures (reportlab's
  `repeatRows=1` always repeats it) and for most real invoicing software,
  but a PDF whose continuation pages omit or vary the header will silently
  under-count. No code fix planned for Phase 0; verify against a real
  multi-page FARO invoice before trusting exact counts on long documents.
- **A PDF with no detectable table gets no DataFrame at all.** The agent
  is told explicitly when this happens (see `NO_TABLE_GUIDANCE` in
  `src/agent.py`) and steered away from guessing, but exact counts are
  genuinely unavailable for such documents — only qualitative
  summarization/lookup from the raw text works.
- **DOCX is out of scope for this prototype**, despite being named in the
  spec (§5) as a real FARO document format. No `.docx` extractor exists yet
  — `extract_document` will return `parse_failed=True` for one.
- **`tools.py`'s `filter_expr` (passed to `pandas.DataFrame.query()`) is
  not sanitized or restricted** — the only input source today is a local
  LLM's tool-call arguments in a single-user local prototype, which is an
  acceptable risk for Phase 0, but this becomes a real security question at
  Phase 2 once real employee documents run on a shared box. **Do not ship
  past Phase 0 without hardening this** (e.g. whitelisting the DataFrame's
  actual column names rather than passing the string straight through).
- **`normalize_numeric_columns` (`src/extractors/dtypes.py`) can strip
  leading zeros from a numeric-looking ID/code column** (e.g. a postal code
  or article code stored as `"00123"` would become `123`). No current test
  fixture has such a column; worth a column-name allowlist/denylist if real
  documents turn out to have one.
- **`requirements.txt` uses `>=`, not pinned versions** — correct for a
  Phase 0 dev prototype, but Phase 1 packaging for the air-gapped Mac
  Studio needs pinned wheels bundled for offline install (per spec §8).
- **The OCR fallback path itself is untested end-to-end on this machine**
  (Tesseract wasn't installed here for most of Phase 0's development) —
  the `lang="deu+eng"` fix is code-reviewed but not verified against a real
  scanned German document. Worth a manual check before Phase 1.

## Full history

Every task, every review finding, and every ruling made during Phase 0's
build is recorded in `.superpowers/sdd/2026-09-18-local-preprocessing-pipeline-phase0/progress.md`
in this repo's git history (the SDD workspace directory is deleted after
completion — recover it from an earlier commit if needed, or just read the
commit log: `git log --oneline`).
