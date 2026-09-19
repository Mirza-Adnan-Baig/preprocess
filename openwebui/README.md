# Open WebUI integration (Phase 1)

**Status: verified working end-to-end (2026-09-19)** against a real local
Open WebUI 0.11.3 instance + real Ollama model — upload → find real file →
extract → tool-call → exact correct answer, no fallback. Verified on a
pip-install; **not yet verified on the Mac Studio's specific setup**
(unclear if it's Docker or pip-install there — see the connection
troubleshooting doc if `FALLBACK_USED` shows up).

**Before installing anywhere: read the "verify your paste actually
replaced the old code" note in
[`../docs/openwebui-connection-troubleshooting.md`](../docs/openwebui-connection-troubleshooting.md).**
A failed full-selection before pasting an update can silently leave old
code in the file, appearing to "not apply" the fix — this cost real
debugging time during verification and is easy to hit again.

Three files here, install in this order (the two diagnostics are optional
now that the real pipe is confirmed working, but still useful if the Mac
Studio's setup behaves differently):

## 0. `streaming_diagnostic_pipe.py` — if the real pipeline hangs/freezes, install this FIRST

Two attempted fixes for freezing (async streaming, then a sync-generator
workaround for a known Open WebUI bug) didn't change the symptom, which
means it's time to isolate the actual cause instead of guessing further.
This does nothing except yield 6 short messages 2 seconds apart, no
document/Ollama/extraction involved — it can't be slow or fail.

**Install:** same steps as below. Select **"Diagnostic: Streaming Timing
Test"** as the model, send any message (no file needed), **watch closely
and time it**.

- **Messages appear one at a time over ~10-12 seconds** → streaming display
  works fine in this Open WebUI setup. The real pipeline's slowness is
  genuine backend processing time (the 27B model, OCR, or a large
  document), not a plumbing bug — tell me this and we'll focus on making
  the actual work faster instead of the UI.
- **Nothing appears for a while, then all 6 lines show up at once** →
  Open WebUI isn't displaying streamed output incrementally in this setup
  at all, regardless of how the pipe is written — a different, more
  fundamental problem than the two fixes so far assumed. Tell me this and
  we'll investigate that instead.

Report back which one you saw — this determines what gets fixed next.

## 1. `diagnostic_pipe.py` — install and run this first

This doesn't extract anything. It just prints exactly what Open WebUI hands
a Pipe Function when you upload a file — the one thing that genuinely
varies by Open WebUI version and can't be verified without a live instance.

**Install:** Open WebUI (as admin) → **Admin Panel → Functions → Create**
→ paste the whole file → **Save** → flip the **Active** toggle on.

**Run it:** start a new chat, pick **"Diagnostic: File Inspector"** as the
model (top of the chat, where you normally pick Qwen), attach a real PDF or
XLSX file, type any question, send. It replies with a JSON dump.

**What to check in the output:**
- `content_length` under `0` or a few hundred → you're only getting a short
  preview/summary, not the real document. `content_length` in the
  thousands+ → you're likely getting Open WebUI's own extracted text (still
  not the raw file, but at least substantial).
- `file_dict_keys` — if there's an `id`, `hash`, or `path`-like field beyond
  `filename`, that's useful; tell me what's there.

## 2. `exact_count_pipe.py` — the real extraction + tool-calling pipeline

Same install steps (**Admin Panel → Functions → Create** → paste → Save →
Active). Select **"Exact Count Document Assistant"** as the model, attach a
file, ask your question.

**What it does:**
1. Tries to find the original uploaded file on disk (a few common Open
   WebUI storage paths — this is the part diagnostic_pipe.py helps verify).
2. If found: runs the exact same extraction logic as this repo's
   `src/extractors/` (tested, 31/31 passing) — real pandas table, exact
   FACTS, and gives the model `count_rows`/`sum_column`/`get_row` tools to
   get exact answers instead of guessing.
3. If NOT found: falls back to whatever text Open WebUI already extracted
   itself. No structured table, no exact-count guarantee — every answer in
   this mode is labeled `(FALLBACK_USED: ...)` at the end so you always
   know which path was taken. Never silently pretends to be exact when it
   isn't.

**Config:** click the function's settings (gear icon) in Admin Panel →
Functions. Two valves:
- `MODEL` — whatever the Mac Studio's model is actually tagged as in
  `ollama list` there (defaults to `qwen3.6:27b`).
- `OLLAMA_HOST` — where to reach Ollama (defaults to `http://localhost:11434`).
  **If you get "failed to connect to Ollama"**, this is almost always the
  wrong value, not a real Ollama problem — see
  [`../docs/openwebui-connection-troubleshooting.md`](../docs/openwebui-connection-troubleshooting.md)
  (short version: Open WebUI running in Docker means `localhost` means the
  container, not the Mac — try `http://host.docker.internal:11434`).

**Verified:** end-to-end against a real local Open WebUI instance — file
found via its own `path` field, extracted, correctly answered via
`count_rows`, no `FALLBACK_USED`. **Not yet verified:** the Mac Studio's
specific setup (Docker vs pip-install there is unconfirmed). If every
answer comes back with `FALLBACK_USED` there, that's the thing to fix next
— run `diagnostic_pipe.py` there and check what `path` (if anything) it
reports, and see the connection troubleshooting doc.

## Why this can't just import the repo's `src/` code directly

Open WebUI Functions are single self-contained files (declared pip
dependencies via the frontmatter `requirements:` line, no local package
imports). So `exact_count_pipe.py` re-implements the same logic that's
already tested in `src/extractors/` and `src/tools.py` — it's duplicated on
purpose, not by accident. If you change the extraction logic in `src/`
later, the same change needs to be mirrored here.
