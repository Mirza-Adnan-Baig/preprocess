# Open WebUI integration (Phase 1 — not yet verified live)

Two files here, install in this order:

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

**Verified so far:** the extraction and tool-calling logic itself was
tested tonight against real synthetic data and a real local model — correct
answers, confirmed. **Not yet verified:** whether `_find_raw_file_on_disk`
actually finds your files on the Mac Studio's specific storage setup. If
every answer comes back with `FALLBACK_USED`, that's the thing to fix next
— tell me what the diagnostic pipe showed and I'll adjust the file-finding
logic to match your actual setup.

## Why this can't just import the repo's `src/` code directly

Open WebUI Functions are single self-contained files (declared pip
dependencies via the frontmatter `requirements:` line, no local package
imports). So `exact_count_pipe.py` re-implements the same logic that's
already tested in `src/extractors/` and `src/tools.py` — it's duplicated on
purpose, not by accident. If you change the extraction logic in `src/`
later, the same change needs to be mirrored here.
