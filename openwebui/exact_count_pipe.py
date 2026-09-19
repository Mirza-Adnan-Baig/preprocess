"""
title: Exact Count Document Assistant
author: Mirza
version: 0.8.1
requirements: pandas, openpyxl, tabulate, pymupdf, pytesseract, Pillow, ollama

Open WebUI Pipe Function. Answers questions about an uploaded PDF/CSV/XLSX
document by extracting it into a real table (pandas) and giving the LLM
tools (count_rows/sum_column/get_row) to compute exact answers, instead of
letting it guess by reading text.

STATUS: fully verified live end-to-end (2026-09-19) against a real local
Open WebUI 0.11.3 instance (pip install) with a real Ollama model. Full
round-trip confirmed correct: uploaded a synthetic 14-line-item invoice,
asked "how many line items", got back exactly "14" with NO FALLBACK_USED
-- meaning the real file was located via its own path, extracted with
PyMuPDF, and counted via the count_rows tool, not guessed or read from
Open WebUI's own weaker text extraction. Streaming and the heartbeat
during model "thinking" time were also confirmed working.

NOT yet verified against the specific Mac Studio deployment (Docker vs
pip-install there is still unconfirmed) -- if FALLBACK_USED appears there,
see docs/openwebui-connection-troubleshooting.md for that environment's
diagnostics.

IMPORTANT for whoever edits this function's code in the Open WebUI admin
UI: after pasting an update, verify it actually replaced the old content
rather than appending after it (scroll to the top of the code editor and
confirm the version number at the top matches what you just pasted, and
that there's only one "class Pipe:" in the file). A failed full-selection
before paste can leave old code appended after the new code -- since
Python executes top-to-bottom, a stale "class Pipe:" appearing later in
the file will silently override the fix you just tried to apply, with no
error and no visible sign in the editor. This exact failure mode cost real
debugging time during initial verification.

Install: Open WebUI admin -> Admin Panel -> Functions -> Create -> paste
this whole file -> Save -> toggle Active -> select "Exact Count Document
Assistant" as the model in a new chat -> upload a file -> ask a question.

If you get a connection error (e.g. "failed to connect to ollama"), open
this function's settings (gear icon, Admin Panel -> Functions) and check
the OLLAMA_HOST valve -- the default assumes Ollama is reachable at
localhost from wherever Open WebUI's own process runs, which is FALSE if
Open WebUI is running inside Docker. See docs/openwebui-connection-troubleshooting.md
in this repo for the full diagnosis.

Mirrors the tested logic in this repo's src/extractors/ and src/tools.py —
see docs/design-spec.md and docs/phase0-plan.md for the full design.
"""

import glob
import io
import json
import os
import queue
import threading
import time

import pandas as pd
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Extraction (mirrors src/extractors/tabular.py, pdf.py, router.py, dtypes.py)
# ---------------------------------------------------------------------------

def _normalize_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        converted = pd.to_numeric(df[col], errors="coerce")
        non_null = df[col].notna().sum()
        if non_null > 0 and converted.notna().sum() == non_null:
            df[col] = converted
    return df


def _detect_header_row(raw: pd.DataFrame, max_scan: int = 10) -> int:
    best_row, best_score = 0, -1
    for i in range(min(max_scan, len(raw))):
        row = raw.iloc[i]
        non_null = row.notna().sum()
        non_numeric_strings = sum(
            isinstance(v, str) and not v.strip().replace(".", "", 1).isdigit()
            for v in row if pd.notna(v)
        )
        score = non_null + non_numeric_strings
        if score > best_score:
            best_score, best_row = score, i
    return best_row


def _compute_facts(df: pd.DataFrame) -> dict:
    facts = {"row_count": len(df), "columns": {}}
    for col in df.columns:
        numeric = pd.to_numeric(df[col], errors="coerce")
        has_numeric = numeric.notna().any()
        facts["columns"][col] = {
            "sum": float(numeric.sum()) if has_numeric else None,
            "min": float(numeric.min()) if has_numeric else None,
            "max": float(numeric.max()) if has_numeric else None,
            "avg": float(numeric.mean()) if has_numeric else None,
            "unique_count": int(df[col].nunique(dropna=True)),
        }
    return facts


def _extract_tabular_bytes(raw_bytes: bytes, is_csv: bool) -> tuple[pd.DataFrame, dict, str]:
    if is_csv:
        text = None
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                text = raw_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")
        import csv
        try:
            delimiter = csv.Sniffer().sniff(text[:4096]).delimiter
        except csv.Error:
            delimiter = ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        width = max((len(r) for r in rows), default=0)
        padded = [r + [None] * (width - len(r)) for r in rows]
        raw = pd.DataFrame(padded)
    else:
        raw = pd.read_excel(io.BytesIO(raw_bytes), header=None)

    header_row = _detect_header_row(raw)
    header = raw.iloc[header_row]
    df = raw.iloc[header_row + 1:].copy()
    df.columns = [str(c) for c in header]
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all").reset_index(drop=True)
    df = _normalize_numeric_columns(df)

    facts = _compute_facts(df)
    markdown = df.to_markdown(index=False)
    return df, facts, markdown


def _extract_pdf_bytes(raw_bytes: bytes) -> tuple[pd.DataFrame | None, dict, str]:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    try:
        page_texts = []
        table_groups: dict[tuple, list[list]] = {}

        for page in doc:
            text = page.get_text()
            if len(text.strip()) < 20:
                text = _ocr_page(page)
            page_texts.append(text)

            for table in page.find_tables().tables:
                rows = table.extract()
                if len(rows) < 2:
                    continue
                header = tuple(rows[0])
                table_groups.setdefault(header, []).extend(rows[1:])

        full_text = "\n\n".join(page_texts)
        facts = {"page_count": len(doc)}

        if not table_groups:
            return None, facts, full_text

        header, body = max(table_groups.items(), key=lambda kv: len(kv[1]))
        df = pd.DataFrame(body, columns=[str(c) for c in header])
        df = df.dropna(axis=0, how="all").reset_index(drop=True)
        df = _normalize_numeric_columns(df)
        facts.update(_compute_facts(df))
        # full_text already contains every table cell as raw page text, so
        # appending the whole thing plus the clean markdown table roughly
        # doubles what the model has to process every round. Keep just a
        # short prefix of full_text (header/company info, dates, footer
        # notes) and let the table (the actual countable data) carry the
        # rest — cuts per-round latency without losing the row data itself.
        text_preview = full_text[:800]
        markdown = text_preview + "\n\n" + df.to_markdown(index=False)
        return df, facts, markdown
    finally:
        doc.close()


def _ocr_page(page) -> str:
    import pytesseract
    from PIL import Image
    pix = page.get_pixmap(dpi=200)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(image, lang="deu+eng")


# ---------------------------------------------------------------------------
# Locating the original uploaded file on disk. Confirmed live (2026-09-19,
# pip-installed Open WebUI 0.11.3): the file record Open WebUI hands us
# already includes the exact stored path in file_info["path"] -- no need to
# guess a directory layout in that case. Kept the directory-guessing
# fallback (including a pip-install-aware open_webui-package-relative path,
# also confirmed live) for older versions or storage backends that don't
# populate "path".
# ---------------------------------------------------------------------------

def _find_raw_file_on_disk(file_info: dict) -> bytes | None:
    direct_path = file_info.get("path")
    if direct_path and os.path.isfile(direct_path):
        with open(direct_path, "rb") as f:
            return f.read()

    file_id = file_info.get("id", "")
    filename = file_info.get("filename", "")

    candidate_dirs = [
        os.environ.get("UPLOAD_DIR", ""),
        os.environ.get("DATA_DIR", ""),
        "/app/backend/data/uploads",
        "./data/uploads",
        "../data/uploads",
    ]
    try:
        import open_webui
        candidate_dirs.append(
            os.path.join(os.path.dirname(open_webui.__file__), "data", "uploads")
        )
    except ImportError:
        pass

    candidate_patterns = [
        f"{file_id}*",
        f"{file_id}_{filename}",
        f"*{file_id}*",
    ]
    for base_dir in candidate_dirs:
        if not base_dir or not os.path.isdir(base_dir):
            continue
        for pattern in candidate_patterns:
            matches = glob.glob(os.path.join(base_dir, pattern))
            if matches:
                with open(matches[0], "rb") as f:
                    return f.read()
    return None


# ---------------------------------------------------------------------------
# Tools + Ollama tool-calling loop (mirrors src/tools.py, src/agent.py)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "count_rows",
        "description": "Count rows in the uploaded table, optionally filtered by a pandas query expression.",
        "parameters": {"type": "object", "properties": {
            "filter_expr": {"type": "string", "description": "Optional pandas query expression"}}},
    }},
    {"type": "function", "function": {
        "name": "sum_column",
        "description": "Sum a numeric column, optionally filtered by a pandas query expression.",
        "parameters": {"type": "object", "properties": {
            "column": {"type": "string"}, "filter_expr": {"type": "string"}}, "required": ["column"]},
    }},
    {"type": "function", "function": {
        "name": "get_row",
        "description": "Get a single row by its zero-based index.",
        "parameters": {"type": "object", "properties": {
            "index": {"type": "integer"}}, "required": ["index"]},
    }},
]

SYSTEM_PROMPT = (
    "You answer questions about one uploaded business document (an invoice "
    "or inventory list). The document's cleaned content is provided below. "
    "For any question involving counting, summing, or totals, you MUST call "
    "the matching tool rather than counting or adding numbers yourself — "
    "the tools compute exact values from the real data; your own counting "
    "over text is not reliable enough for this task. Respond in German by "
    "default, matching the language of the document and the user, unless "
    "the user's question is written in a different language."
)


CONNECTION_HELP = (
    "This is a connectivity/config problem, not a document-parsing one. Check:\n"
    "- Is Ollama actually running on the Mac Studio? (`ollama list` in Terminal there)\n"
    "- Does the model name above exactly match `ollama list`'s output?\n"
    "- **If Open WebUI runs in Docker**, `localhost`/`127.0.0.1` inside the container is "
    "NOT the Mac itself — try `http://host.docker.internal:11434` as the OLLAMA_HOST "
    "valve instead (Admin Panel > Functions > this function's gear icon).\n"
    "- If Ollama is ALSO in a Docker container on the same network as Open WebUI, use "
    "that container's service name instead, e.g. `http://ollama:11434`."
)


_HEARTBEAT = object()
_STREAM_DONE = object()


def _chat_stream_with_heartbeat(client, model: str, messages: list, tools, heartbeat_seconds: float = 3.0):
    """Wraps client.chat(..., stream=True) so the caller gets a heartbeat
    signal every `heartbeat_seconds` while waiting for the next chunk,
    instead of blocking silently. Ollama has to fully process the prompt
    (prefill) before emitting the first token — for a large model with a
    real document, that wait alone can run well past a minute, during
    which a plain `for chunk in stream:` loop yields nothing at all. That
    silent gap is what was tripping the disconnect, even though token
    streaming itself works fine once it starts. Runs the actual HTTP call
    on a background thread so this generator can keep polling and yielding
    heartbeats in the meantime."""
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for chunk in client.chat(model=model, messages=messages, tools=tools, stream=True):
                q.put(chunk)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(_STREAM_DONE)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        try:
            item = q.get(timeout=heartbeat_seconds)
        except queue.Empty:
            yield _HEARTBEAT
            continue
        if item is _STREAM_DONE:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def _run_tool_loop(model: str, host: str, df: pd.DataFrame | None, context: str, question: str):
    """Plain (NOT async) generator: yields text chunks as they arrive from
    Ollama, so the connection to the browser stays alive throughout a long
    response instead of going silent for the whole duration of one blocking
    call. Deliberately a sync generator, not an async one — Open WebUI
    (confirmed in v0.6.43, see open-webui/open-webui#20196) does not
    reliably send a completion signal for AsyncGenerator-based pipes, which
    leaves the UI stuck showing "executing" forever even after the model has
    actually finished. A plain sync generator is the confirmed community
    workaround. Revisit this once that upstream bug is fixed."""
    import ollama

    client = ollama.Client(host=host)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"DOCUMENT:\n{context}\n\nQUESTION: {question}"},
    ]
    tools = TOOL_SCHEMAS if df is not None else None
    max_rounds = 6

    for round_num in range(max_rounds):
        full_content = ""
        tool_calls = None
        round_start = time.monotonic()
        first_chunk_seen = False
        try:
            for item in _chat_stream_with_heartbeat(client, model, messages, tools):
                if item is _HEARTBEAT:
                    if not first_chunk_seen:
                        waited = time.monotonic() - round_start
                        yield f"_(still thinking, {waited:.0f}s...)_ "
                    continue
                first_chunk_seen = True
                piece = item.get("message", {}).get("content", "")
                if piece:
                    full_content += piece
                    yield piece
                if item.get("message", {}).get("tool_calls"):
                    tool_calls = item["message"]["tool_calls"]
        except Exception as exc:
            yield (
                f"\n\nCould not reach Ollama at `{host}` (model `{model}`): {exc}\n\n"
                + CONNECTION_HELP
            )
            return
        round_elapsed = time.monotonic() - round_start

        messages.append({"role": "assistant", "content": full_content, "tool_calls": tool_calls})

        if not tool_calls:
            return  # full_content has already been streamed above

        yield f"\n\n_(round {round_num + 1}/{max_rounds}, {round_elapsed:.0f}s — model requested {len(tool_calls)} tool call(s):_\n"

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            try:
                if name == "count_rows":
                    subset = df.query(args["filter_expr"]) if args.get("filter_expr") else df
                    result = len(subset)
                elif name == "sum_column":
                    subset = df.query(args["filter_expr"]) if args.get("filter_expr") else df
                    numeric = pd.to_numeric(subset[args["column"]], errors="coerce")
                    if len(subset) > 0 and numeric.notna().sum() == 0:
                        raise ValueError(f"Column '{args['column']}' has no numeric values to sum")
                    result = float(numeric.sum())
                elif name == "get_row":
                    result = df.iloc[args["index"]].to_dict()
                else:
                    result = f"unknown tool: {name}"
            except Exception as exc:
                result = {"error": str(exc)}

            yield f"_- `{name}({args})` -> `{result}`_\n"
            messages.append({"role": "tool", "content": json.dumps(result, default=str), "tool_name": name})

        yield "_)_\n\n"

    yield (
        f"\n\nCouldn't reach a final answer within {max_rounds} tool-call rounds — "
        "see the trace above for what the model tried. If it kept repeating the same "
        "or similar tool calls without ever giving a plain answer, that's worth reporting "
        "back (may need a clearer question, or the model needs more rounds for this document)."
    )


# ---------------------------------------------------------------------------
# Pipe entry point
# ---------------------------------------------------------------------------

class Pipe:
    class Valves(BaseModel):
        MODEL: str = "qwen3.6:27b"
        OLLAMA_HOST: str = "http://localhost:11434"

    def __init__(self):
        self.id = "exact_count_document_assistant"
        self.name = "Exact Count Document Assistant"
        self.valves = self.Valves()

    def pipe(self, body: dict, __files__: list = None, __user__: dict = None):
        user_message = body.get("messages", [{}])[-1].get("content", "")

        if not __files__:
            yield "Please attach a PDF, CSV, or XLSX file with your question."
            return

        f = __files__[0]
        file_info = f.get("file", {})
        filename = file_info.get("filename", "")
        file_id = file_info.get("id", "")
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

        yield "_(extracting document...)_\n\n"

        raw_bytes = _find_raw_file_on_disk(file_info)
        fallback_used = raw_bytes is None

        try:
            if raw_bytes is not None and ext in ("csv", "xlsx", "xls"):
                df, facts, markdown = _extract_tabular_bytes(raw_bytes, is_csv=(ext == "csv"))
            elif raw_bytes is not None and ext == "pdf":
                df, facts, markdown = _extract_pdf_bytes(raw_bytes)
            elif fallback_used:
                # Couldn't find the raw file — fall back to whatever text
                # Open WebUI already extracted. No structured table, no
                # tool-calling guarantee — flagged to the user below.
                df, facts = None, None
                markdown = file_info.get("data", {}).get("content", "") or ""
            else:
                yield f"Couldn't parse this file (unsupported type: .{ext})."
                return
        except Exception as e:
            yield f"Couldn't fully parse this file: {e}"
            return

        context = markdown
        if facts:
            context += f"\n\nFACTS: {json.dumps(facts, ensure_ascii=False)}"
        if df is None:
            context += (
                "\n\nNOTE: The original file couldn't be located on disk, so this is "
                "Open WebUI's own pre-extracted text, not a verified structured table. "
                "Exact counts are NOT guaranteed here — say so if a count is asked."
            )

        for chunk in _run_tool_loop(self.valves.MODEL, self.valves.OLLAMA_HOST, df, context, user_message):
            yield chunk

        if fallback_used:
            yield (
                "\n\n_(FALLBACK_USED: raw file not found on disk — this answer used "
                "Open WebUI's own extracted text, not the verified extraction pipeline. "
                "Treat exact numbers with caution.)_"
            )
