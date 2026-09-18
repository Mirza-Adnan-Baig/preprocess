"""
title: Exact Count Document Assistant
author: Mirza
version: 0.1.0
requirements: pandas, openpyxl, tabulate, pymupdf, pytesseract, Pillow, ollama

Open WebUI Pipe Function. Answers questions about an uploaded PDF/CSV/XLSX
document by extracting it into a real table (pandas) and giving the LLM
tools (count_rows/sum_column/get_row) to compute exact answers, instead of
letting it guess by reading text.

STATUS: best-effort, NOT verified against a live Open WebUI instance yet.
Run diagnostic_pipe.py FIRST and confirm what Open WebUI actually gives us
for an uploaded file before trusting this. The core uncertainty: this pipe
tries to find the ORIGINAL uploaded file on disk (needed for real table
extraction); if that fails, it falls back to whatever text Open WebUI's own
document loader already extracted, which is weaker (no guaranteed exact
counts) — the FALLBACK_USED note in every answer tells you which happened.

Install: Open WebUI admin -> Admin Panel -> Functions -> Create -> paste
this whole file -> Save -> toggle Active -> select "Exact Count Document
Assistant" as the model in a new chat -> upload a file -> ask a question.

Mirrors the tested logic in this repo's src/extractors/ and src/tools.py —
see docs/design-spec.md and docs/phase0-plan.md for the full design.
"""

import glob
import io
import json
import os

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
        markdown = full_text + "\n\n" + df.to_markdown(index=False)
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
# Locating the original uploaded file on disk (the uncertain part — see the
# module docstring). Tries a few common Open WebUI storage layouts; if none
# match, the pipe falls back to Open WebUI's own pre-extracted text.
# ---------------------------------------------------------------------------

def _find_raw_file_on_disk(file_id: str, filename: str) -> bytes | None:
    candidate_dirs = [
        os.environ.get("UPLOAD_DIR", ""),
        os.environ.get("DATA_DIR", ""),
        "/app/backend/data/uploads",
        "./data/uploads",
        "../data/uploads",
    ]
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


def _run_tool_loop(model: str, df: pd.DataFrame | None, context: str, question: str) -> str:
    import ollama

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"DOCUMENT:\n{context}\n\nQUESTION: {question}"},
    ]
    tools = TOOL_SCHEMAS if df is not None else None

    for _ in range(4):
        response = ollama.chat(model=model, messages=messages, tools=tools)
        message = response["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return message["content"]

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

            messages.append({"role": "tool", "content": json.dumps(result, default=str), "tool_name": name})

    return "Couldn't reach a final answer within the tool-call budget."


# ---------------------------------------------------------------------------
# Pipe entry point
# ---------------------------------------------------------------------------

class Pipe:
    class Valves(BaseModel):
        MODEL: str = "qwen3.6:27b"

    def __init__(self):
        self.id = "exact_count_document_assistant"
        self.name = "Exact Count Document Assistant"
        self.valves = self.Valves()

    async def pipe(self, body: dict, __files__: list = None, __user__: dict = None) -> str:
        user_message = body.get("messages", [{}])[-1].get("content", "")

        if not __files__:
            return "Please attach a PDF, CSV, or XLSX file with your question."

        f = __files__[0]
        file_info = f.get("file", {})
        filename = file_info.get("filename", "")
        file_id = file_info.get("id", "")
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

        raw_bytes = _find_raw_file_on_disk(file_id, filename)
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
                return f"Couldn't parse this file (unsupported type: .{ext})."
        except Exception as e:
            return f"Couldn't fully parse this file: {e}"

        context = markdown
        if facts:
            context += f"\n\nFACTS: {json.dumps(facts, ensure_ascii=False)}"
        if df is None:
            context += (
                "\n\nNOTE: The original file couldn't be located on disk, so this is "
                "Open WebUI's own pre-extracted text, not a verified structured table. "
                "Exact counts are NOT guaranteed here — say so if a count is asked."
            )

        answer = _run_tool_loop(self.valves.MODEL, df, context, user_message)

        if fallback_used:
            answer += (
                "\n\n_(FALLBACK_USED: raw file not found on disk — this answer used "
                "Open WebUI's own extracted text, not the verified extraction pipeline. "
                "Treat exact numbers with caution.)_"
            )
        return answer
