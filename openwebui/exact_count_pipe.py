"""
title: Exact Count Document Assistant
author: Mirza
version: 0.9.2
requirements: pandas, openpyxl, tabulate, pymupdf, pytesseract, Pillow, ollama

Open WebUI Pipe Function. Answers questions about an uploaded document of
ANY kind -- not just invoices. Extracts every distinct table it can find
(a single upload can legitimately contain more than one, e.g. two
invoices merged into one scan) into real pandas tables, gives the LLM
tools to compute exact counts/sums against them instead of guessing, and
always includes the full document text so it can also answer general
questions (summarize this, what does clause 4 say, who is the sender)
that have nothing to do with counting.

STATUS (2026-09-19/20): core single-table path fully verified live
end-to-end against a real local Open WebUI 0.11.3 instance (pip install)
with a real Ollama model -- uploaded a synthetic 14-line-item invoice,
asked "how many line items", got back exactly "14" with NO FALLBACK_USED.
Streaming and the heartbeat during model "thinking" time confirmed
working. The multi-table extraction (list_tables + per-table tool calls)
and full-text-always inclusion (v0.9.0) are now ALSO verified live
through the actual Open WebUI chat UI (v0.9.0/0.9.1 pushed via the
REST-API update method, not browser paste), with synthetic 250+200-row
two-table stress tests:
  - Different-header tables (e.g. two shipments with different columns):
    correctly kept as separate tables, not merged or silently dropped.
    Cross-table total ("how many rows in total") correct (450) via a
    code-computed FACTS._summary.total_rows_all_tables -- earlier, before
    that fix, the small test model added the two per-table counts itself
    and got it WRONG (430) despite having the right numbers in front of
    it, which is exactly the kind of mistake tool-calling exists to avoid.
    Confirmed live through the UI: "450 rows... table_1... table_2" with
    correct column names for each.
  - Same-header tables: correctly merge into one combined table (1 table,
    450 rows -- confirmed via direct script run against the real
    extractor). BUT found live through the UI (2026-09-20): asked "how
    many rows total, and how many distinct tables are there?" -- got the
    row count right (450) but the table count WRONG ("three different
    tables" vs the actual 1). A prompt tweak alone (v0.9.1, requiring
    list_tables for table-count questions) did NOT fix it -- the model
    still answered wrong afterward. Root-caused with a temporary debug
    build that printed the raw `user_message` this pipe actually
    receives: **Open WebUI's own built-in file RAG was silently
    rewriting the last user message before this pipe ever saw it** --
    replacing the real question with its own ~3.4K-char citation-prompt
    template plus independently-chunked retrieval from the same file,
    completely bypassing this pipe's own extraction. This happens for
    ANY selected model (custom Pipe or not) whenever a file is attached,
    unless the model explicitly opts out, because Open WebUI's per-model
    capability `file_context` defaults to True. FIXED (v0.9.2) by
    creating a Workspace Model override for this pipe's id with
    `meta.capabilities.file_context = False` (see CRITICAL DEPLOYMENT
    STEP below) -- re-verified live afterward: the pipe now receives the
    real short question (confirmed via the same debug build: `user_message`
    was just the `<attached_files>` tag plus the literal question, no RAG
    template), and the model correctly answered "1 distinct table, 450
    rows." This was a real, previously-undiscovered architecture gap that
    had nothing to do with this pipe's own extraction/tool-calling logic
    -- it explains why free-text/qualitative answers were sometimes
    unreliable even though the raw extraction was already verified
    correct in isolation.
  - Qualitative recall on a long, table-dense document: earlier testing
    (v0.9.0/0.9.1) that found the small test model (qwen2.5:7b) sometimes
    failed to name companies mentioned in the text was very likely
    ALSO downstream of this same file_context RAG interference (the
    model was partly answering from Open WebUI's own retrieved chunks,
    not this pipe's full-text context). Worth re-confirming this
    specific question is now consistently correct with file_context
    disabled, and still worth re-checking against the actual production
    model (Qwen3.6-27B) before fully trusting "any document, extract the
    understanding" on genuinely long, dense files -- exact counts via
    tool-calling were never affected by this, since those don't depend on
    the model reading prose.

CRITICAL DEPLOYMENT STEP -- required on every Open WebUI instance this
pipe is installed on, including the Mac Studio, or file-based questions
will silently get corrupted context: Open WebUI's built-in file RAG
(the `file_context` model capability, default True) intercepts every
attached file and rewrites the user's last message into its own
citation-prompt template before ANY pipe function runs, regardless of
this file being handled correctly by `_find_raw_file_on_disk` /
`_extract_pdf_bytes` etc. This must be disabled for this specific model
id, once, after installing/updating this function:
  1. Sign in as admin, then call (or use Admin Panel > Models UI once
     that capability toggle is exposed there):
     POST /api/v1/models/create
     {"id": "exact_count_document_assistant",
      "name": "Exact Count Document Assistant",
      "meta": {"capabilities": {"file_context": false}},
      "params": {}, "is_active": true}
     (If a Models entry with this id already exists, use
     POST /api/v1/models/model/update instead of /create.)
  2. Force the server to pick it up immediately (it otherwise only
     reloads this cache lazily): GET /api/models?refresh=true
Without this, `__files__` is still delivered correctly to this pipe
(so exact counts via tool-calling still work, since those never depend
on the literal question text), but the free-text `user_message` the
pipe uses to steer qualitative answers may be Open WebUI's own rewritten
RAG prompt instead of the real question -- producing plausible-looking
but wrong free-text answers with no visible error anywhere.

NOT yet verified against the specific Mac Studio deployment (Docker vs
pip-install there is still unconfirmed, and the file_context override
above has not yet been applied/tested there) -- if FALLBACK_USED appears
there, see docs/openwebui-connection-troubleshooting.md for that
environment's diagnostics.

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


def _tables_to_facts_and_markdown(tables: dict) -> tuple[dict, str]:
    """Shared by both extractors: given {table_id: DataFrame}, compute
    per-table facts and a labeled markdown block for each. Table order is
    largest-first, but that's just a convenience — the model can address
    any of them by id (see list_tables), so multi-table documents (e.g.
    two invoices merged into one upload) don't lose data to whichever
    table happened to be biggest."""
    facts = {}
    blocks = []
    for table_id, df in tables.items():
        facts[table_id] = _compute_facts(df)
        blocks.append(
            f"### {table_id} (columns: {', '.join(df.columns)})\n\n"
            + df.to_markdown(index=False)
        )
    if len(tables) > 1:
        # A cross-table total is a compound question (add up several tool
        # results), and small models are unreliable at even simple mental
        # arithmetic — so compute the one cross-table number that's always
        # well-defined (a row count, regardless of differing column names)
        # here in code, rather than trusting the model to add two FACTS
        # numbers correctly on its own. Column sums aren't included here
        # since column names can genuinely differ between tables (e.g.
        # "Unit Price" vs "Cost") and summing mismatched columns would be
        # its own silent-wrong-answer risk.
        facts["_summary"] = {
            "table_count": len(tables),
            "total_rows_all_tables": sum(len(df) for df in tables.values()),
        }
    return facts, "\n\n".join(blocks)


def _extract_tabular_bytes(raw_bytes: bytes, is_csv: bool) -> tuple[dict, dict, str]:
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

    tables = {"table_1": df}
    facts, markdown = _tables_to_facts_and_markdown(tables)
    return tables, facts, markdown


def _extract_pdf_bytes(raw_bytes: bytes) -> tuple[dict, dict, str]:
    """Detects EVERY distinct table in the document (grouped by matching
    header row across pages, so a table spanning multiple pages still
    merges correctly) rather than picking only the single largest one.
    A real-world upload is often more than one logical document merged
    into a single file (e.g. two invoices scanned together) -- silently
    keeping only the biggest table would drop the rest, which is exactly
    the kind of confidently-incomplete answer this project exists to
    avoid. Full page text is always included (not just a short preview)
    so the model can also answer non-tabular questions about the
    document's actual content, not just counts."""
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
            return {}, facts, full_text

        tables = {}
        for i, (header, body) in enumerate(
            sorted(table_groups.items(), key=lambda kv: -len(kv[1])), start=1
        ):
            df = pd.DataFrame(body, columns=[str(c) for c in header])
            df = df.dropna(axis=0, how="all").reset_index(drop=True)
            df = _normalize_numeric_columns(df)
            tables[f"table_{i}"] = df

        table_facts, table_markdown = _tables_to_facts_and_markdown(tables)
        facts.update(table_facts)
        markdown = full_text + "\n\n" + table_markdown
        return tables, facts, markdown
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
        "name": "list_tables",
        "description": "List every table detected in the document, with each one's id, column names, and row count. Call this whenever the document might contain more than one distinct table (e.g. several invoices, shipments, or sections merged into one upload) before assuming a count covers the whole document.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "count_rows",
        "description": "Count rows in one specific table, optionally filtered by a pandas query expression. Pass table='all' (no filter) to get the total row count across every table in the document in one call, instead of adding up individual table counts yourself.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Table id from list_tables (e.g. 'table_1'), or 'all' for the unfiltered cross-table total"},
            "filter_expr": {"type": "string", "description": "Optional pandas query expression (not supported together with table='all')"}},
            "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "sum_column",
        "description": "Sum a numeric column in one specific table, optionally filtered by a pandas query expression.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Table id from list_tables, e.g. 'table_1'"},
            "column": {"type": "string"}, "filter_expr": {"type": "string"}},
            "required": ["table", "column"]},
    }},
    {"type": "function", "function": {
        "name": "get_row",
        "description": "Get a single row by its zero-based index from one specific table.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Table id from list_tables, e.g. 'table_1'"},
            "index": {"type": "integer"}},
            "required": ["table", "index"]},
    }},
]

SYSTEM_PROMPT = (
    "You answer questions about one uploaded document. It can be anything — "
    "an invoice, a contract, a report, a letter, mixed content — not just "
    "business/tabular documents. The document's full extracted text is "
    "provided below, along with any tables that were detected in it. Your "
    "job is to surface whatever is actually useful from the document for "
    "the question asked, whether that's an exact number, a summary, or a "
    "specific fact buried in the text.\n\n"
    "A single uploaded file can contain MORE THAN ONE distinct table — for "
    "example, two invoices or shipments merged into one scan. Never assume "
    "a count from one table covers the whole document; if in doubt, call "
    "list_tables first to see what's actually there, and combine or report "
    "per-table results as the question requires. If a FACTS._summary block "
    "is present, its total_rows_all_tables value IS the exact cross-table "
    "row total, already computed for you — use it directly (or call "
    "count_rows with table='all') for a whole-document row count. Never "
    "add up individual table counts yourself by hand — that arithmetic is "
    "exactly the kind of mistake this tool-calling design exists to avoid.\n\n"
    "For any question involving counting, summing, totals, or the NUMBER "
    "OF TABLES/DOCUMENTS in the file, you MUST call the matching tool "
    "(list_tables for how-many-tables questions) rather than counting or "
    "guessing yourself — the tools reflect the real detected structure; "
    "your own impression from skimming the text is not reliable enough for "
    "this task, and guessing a plausible-sounding number of tables when "
    "you haven't actually called list_tables is exactly the kind of "
    "confident-but-wrong answer this design exists to avoid. For "
    "qualitative questions (what does this say, summarize this, find X), "
    "answer directly from the document text provided — no tool call needed "
    "for those. Respond in German by default, matching the language of the "
    "document and the user, unless the user's question is written in a "
    "different language."
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


def _run_tool_loop(model: str, host: str, tables: dict, context: str, question: str):
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
    tools = TOOL_SCHEMAS if tables else None
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
                if name == "list_tables":
                    result = {
                        tid: {"columns": list(df.columns), "row_count": len(df)}
                        for tid, df in tables.items()
                    }
                elif name == "count_rows" and args.get("table") == "all":
                    # Well-defined regardless of differing column names
                    # across tables, unlike a cross-table sum_column would
                    # be — computed here rather than asking the model to
                    # add several per-table results itself.
                    if args.get("filter_expr"):
                        raise ValueError(
                            "table='all' only supports an unfiltered count_rows "
                            "(filters may reference columns that don't exist in every "
                            "table) — call count_rows per table id for a filtered count."
                        )
                    result = sum(len(df) for df in tables.values())
                elif name in ("count_rows", "sum_column", "get_row"):
                    table_id = args.get("table")
                    if table_id not in tables:
                        raise ValueError(
                            f"Unknown table '{table_id}' — call list_tables to see valid ids: "
                            f"{list(tables.keys())}"
                        )
                    df = tables[table_id]
                    if name == "count_rows":
                        subset = df.query(args["filter_expr"]) if args.get("filter_expr") else df
                        result = len(subset)
                    elif name == "sum_column":
                        subset = df.query(args["filter_expr"]) if args.get("filter_expr") else df
                        numeric = pd.to_numeric(subset[args["column"]], errors="coerce")
                        if len(subset) > 0 and numeric.notna().sum() == 0:
                            raise ValueError(f"Column '{args['column']}' has no numeric values to sum")
                        result = float(numeric.sum())
                    else:  # get_row
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
                tables, facts, markdown = _extract_tabular_bytes(raw_bytes, is_csv=(ext == "csv"))
            elif raw_bytes is not None and ext == "pdf":
                tables, facts, markdown = _extract_pdf_bytes(raw_bytes)
            elif fallback_used:
                # Couldn't find the raw file — fall back to whatever text
                # Open WebUI already extracted. No structured table(s), no
                # tool-calling guarantee — flagged to the user below.
                tables, facts = {}, None
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
        if not tables:
            context += (
                "\n\nNOTE: No structured table was found (or the original file couldn't "
                "be located, in which case this is Open WebUI's own pre-extracted text). "
                "Exact counts/sums are NOT available — answer only from the text above, "
                "and say so plainly if a count is being requested."
            )

        for chunk in _run_tool_loop(self.valves.MODEL, self.valves.OLLAMA_HOST, tables, context, user_message):
            yield chunk

        if fallback_used:
            yield (
                "\n\n_(FALLBACK_USED: raw file not found on disk — this answer used "
                "Open WebUI's own extracted text, not the verified extraction pipeline. "
                "Treat exact numbers with caution.)_"
            )
