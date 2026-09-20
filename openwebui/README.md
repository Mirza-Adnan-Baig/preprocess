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
Studio's setup behaves differently). Whichever pipe you install, you must
also run the one-time `setup_openwebui` step in §3 below — skipping it
leaves Open WebUI's built-in file RAG on, which silently rewrites the
user's question before the pipe ever sees it.

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

## 2. `faro_document_assistant.py` — the real extraction + tool-calling pipeline (GENERATED, do not hand-edit)

**This file is generated.** It is produced from `src/faro_docs/` and
`adapters/openwebui_pipe.py` by:

```bash
python -m tools.build_bundle
```

Never edit `openwebui/faro_document_assistant.py` directly — any change made
by hand is silently overwritten the next time someone runs
`tools.build_bundle`, and a test in this repo asserts the checked-in bundle
is current with `src/faro_docs/`. Change the logic in `src/faro_docs/` (or
the adapter in `adapters/openwebui_pipe.py`) and regenerate instead.

Same install steps as above (**Admin Panel → Functions → Create** → paste
the generated file's contents → Save → Active). Select
**"FARO Dokument-Assistent"** as the model, attach a file, ask your
question.

**What it does:**
1. Every file attached to the chat message is read directly (via Open
   WebUI's own file-storage path if present, else a few common upload
   directories — this is the part `diagnostic_pipe.py` helps verify).
2. Each file is ingested through `src/faro_docs/ingest/` (CSV, Excel, PDF,
   text) into a real pandas table plus a code-computed FACTS block — German
   number formats, multiple sheets, and totals rows are all handled before
   the model ever sees the data.
3. The model gets tools (`list_documents`, `list_tables`, `count_rows`,
   `sum_column`, `get_row`, `find_rows`) backed by those real tables, so
   counts and sums are exact, never guessed.
4. If no file could be read, or a file has no detectable table, the pipe
   says so explicitly rather than silently falling back to a worse answer.

**Config:** click the function's settings (gear icon) in Admin Panel →
Functions. Three valves:
- `MODEL` — whatever the Mac Studio's model is actually tagged as in
  `ollama list` there (defaults to `qwen3.6:27b`).
- `OLLAMA_HOST` — where to reach Ollama (defaults to `http://localhost:11434`).
  **If you get "failed to connect to Ollama"**, this is almost always the
  wrong value, not a real Ollama problem — see
  [`../docs/openwebui-connection-troubleshooting.md`](../docs/openwebui-connection-troubleshooting.md)
  (short version: Open WebUI running in Docker means `localhost` means the
  container, not the Mac — try `http://host.docker.internal:11434`).
- `MAX_TEXT_CHARS` — how much raw document text is sent to the model before
  it gets truncated (default 40000); tables and FACTS are unaffected.

**Verified:** end-to-end against a real local Open WebUI instance. **Not
yet verified:** the Mac Studio's specific setup (Docker vs pip-install
there is unconfirmed) — run `diagnostic_pipe.py` there first if answers
look wrong.

## 3. Mandatory one-time step: `python -m tools.setup_openwebui`

Open WebUI has a built-in "file RAG" capability that, unless explicitly
disabled per-model, intercepts any chat message with an attached file and
silently rewrites the user's question into its own retrieval prompt
(recognizable by a `### Task:` header and an `<user_query>` wrapper) before
the pipe function ever runs. Without this step, the pipe answers a
question the user never asked — confidently and wrongly, with no error.

After uploading `faro_document_assistant.py` and activating it, run once
per Open WebUI instance:

```bash
python -m tools.setup_openwebui --url http://localhost:3000 --email <admin-email> --password '<admin-password>'
```

This calls Open WebUI's admin API to set `capabilities.file_context = false`
on the `faro_document_assistant` model, then forces Open WebUI to refresh
its model cache (`/api/models?refresh=true`) — the cache is loaded lazily,
so without that refresh call the change silently does nothing until the
server is restarted. As a second line of defense, the pipe itself detects
the `### Task:` / `<user_query>` markers at runtime and prints a warning in
the chat if `file_context` is still on — if you ever see that warning,
this step was skipped or didn't take effect; re-run it.
