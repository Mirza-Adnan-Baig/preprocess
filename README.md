# local-llm-pipeline

Prototype document-preprocessing pipeline for the FARO local-LLM project.
Runs entirely on local hardware with synthetic test data — no dependency on
the Mac Studio or real company documents. See
`../superpowers/specs/2026-09-18-local-llm-document-preprocessing-design.md`
for the full design and `../superpowers/plans/2026-09-18-local-preprocessing-pipeline-phase0.md`
for the implementation plan this was built from.

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
