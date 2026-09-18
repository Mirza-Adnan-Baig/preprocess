# Local LLM Document Preprocessing — Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and validate, entirely on the author's personal PC (no Mac Studio access required), the core document-preprocessing pipeline: deterministic extraction of PDF/CSV/XLSX into clean text + an exact FACTS block, callable tools backed by the real data, and an end-to-end test proving a local LLM answers counting/aggregation questions correctly using tools instead of guessing.

**Architecture:** A small Python package (`local-llm-pipeline/`) with a file-type router dispatching to a tabular extractor (pandas-based, header-detection + cleaning + FACTS) and a PDF extractor (PyMuPDF text/table extraction with OCR fallback for image-only pages). Extracted data backs three callable tools (`count_rows`, `sum_column`, `get_row`) exposed to a locally-running Ollama model via its native tool-calling API. Synthetic invoice/inventory data (generated in-repo, no external dataset dependency) is used for tests so nothing here depends on real FARO documents or Mac Studio access.

**Tech Stack:** Python 3.11+, pandas, openpyxl, tabulate, PyMuPDF (`pymupdf`), pytesseract + Pillow (OCR fallback — requires the Tesseract binary installed separately), reportlab (synthetic PDF generation for tests), `ollama` Python client, pytest.

**Spec:** [design-spec.md](design-spec.md) — this plan implements Phase 0 of that spec (§8).

## Global Constraints

- No dependency on Mac Studio, Open WebUI, or real FARO documents — everything here runs and is tested on the author's own PC.
- Counting/summing answers must come from code (pandas), never from the LLM reading text — per spec §6.
- Unparseable files must produce an explicit "couldn't fully parse this file" result, never a silent guess — per spec §5.
- Keep runtime dependencies lean (no GPU-only libraries, no heavyweight embedding/vision models) — per spec §7, since this logic will eventually run alongside Ollama on shared memory.

---

## File Structure

```
local-llm-pipeline/
  src/
    extractors/
      tabular.py     — CSV/XLSX header-detection, cleaning, FACTS computation
      pdf.py         — PDF text/table extraction, OCR fallback
      router.py      — file-type dispatch, unified ExtractionResult
    tools.py         — count_rows / sum_column / get_row, backed by a DataFrame
    agent.py         — Ollama tool-calling loop: question + context + tools -> answer
  tests/
    test_tabular.py
    test_pdf.py
    test_router.py
    test_tools.py
    test_agent_e2e.py
  scripts/
    generate_synthetic_data.py  — builds test fixtures (messy CSV/XLSX, PDF invoice)
  requirements.txt
  README.md
```

---

### Task 1: Project scaffolding + synthetic test data generator

**Files:**
- Create: `local-llm-pipeline/requirements.txt`
- Create: `local-llm-pipeline/README.md`
- Create: `local-llm-pipeline/scripts/generate_synthetic_data.py`
- Create: `local-llm-pipeline/tests/test_synthetic_data.py`

**Interfaces:**
- Produces: `generate_messy_inventory_xlsx(path: str, n_rows: int, seed: int = 0) -> None` — writes an XLSX with a title row, a blank row, then a header row, then `n_rows` data rows, with ~5% randomly blanked cells.
- Produces: `generate_invoice_pdf(path: str, n_line_items: int, seed: int = 0) -> None` — writes a PDF with a header block (company name, invoice number), a line-item table with `n_line_items` rows, and a footer text block.
- Both are used by every later test task as fixtures — signatures must not change.

- [ ] **Step 1: Create the project skeleton and pin dependencies**

```bash
mkdir -p "I:/docs/local-llm-pipeline/src/extractors" "I:/docs/local-llm-pipeline/tests" "I:/docs/local-llm-pipeline/scripts"
cd "I:/docs/local-llm-pipeline" && git init
```

Create `local-llm-pipeline/requirements.txt`:

```
pandas>=2.2
openpyxl>=3.1
tabulate>=0.9
pymupdf>=1.24
pytesseract>=0.3.10
Pillow>=10.3
reportlab>=4.2
ollama>=0.4
pytest>=8.2
```

Create `local-llm-pipeline/README.md`:

```markdown
# local-llm-pipeline

Prototype document-preprocessing pipeline for the FARO local-LLM project.
Runs entirely on local hardware with synthetic test data — no dependency on
the Mac Studio or real company documents. See
`docs/superpowers/specs/2026-09-18-local-llm-document-preprocessing-design.md`
in the parent repo for the full design.

## Setup

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

Install the Tesseract OCR binary separately (required for scanned-PDF
fallback): https://github.com/tesseract-ocr/tesseract

## Test

    pytest -v
```

- [ ] **Step 2: Write the failing test for synthetic data generation**

Create `local-llm-pipeline/tests/test_synthetic_data.py`:

```python
import pandas as pd
from scripts.generate_synthetic_data import generate_messy_inventory_xlsx, generate_invoice_pdf


def test_generate_messy_inventory_xlsx_row_count(tmp_path):
    out = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(out), n_rows=50, seed=1)

    raw = pd.read_excel(out, header=None)
    # title row + blank row + header row + 50 data rows
    assert len(raw) == 53
    assert out.exists()


def test_generate_invoice_pdf_creates_file(tmp_path):
    out = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(out), n_line_items=30, seed=1)

    assert out.exists()
    assert out.stat().st_size > 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_synthetic_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts'`

- [ ] **Step 4: Implement the synthetic data generator**

Create `local-llm-pipeline/scripts/__init__.py` (empty file).

Create `local-llm-pipeline/scripts/generate_synthetic_data.py`:

```python
import random

import pandas as pd
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

ARTICLE_NAMES = [
    "iPhone 13 Display", "Samsung S22 Battery", "USB-C Charging Port",
    "iPhone Back Glass", "Pixel 7 Camera Module", "Phone Case Clear",
    "Screen Protector Tempered", "Lightning Cable 1m", "Wireless Charger Pad",
    "Replacement SIM Tray",
]


def generate_messy_inventory_xlsx(path: str, n_rows: int, seed: int = 0) -> None:
    rng = random.Random(seed)
    wb = Workbook()
    ws = wb.active

    ws.append(["FARO Inventory Export"])
    ws.append([])
    ws.append(["Article", "Quantity", "Unit Price EUR", "Supplier"])

    for i in range(n_rows):
        row = [
            rng.choice(ARTICLE_NAMES),
            rng.randint(1, 200),
            round(rng.uniform(2.5, 150.0), 2),
            f"Supplier {rng.randint(1, 5)}",
        ]
        if rng.random() < 0.05:
            blank_col = rng.randint(0, 3)
            row[blank_col] = None
        ws.append(row)

    wb.save(path)


def generate_invoice_pdf(path: str, n_line_items: int, seed: int = 0) -> None:
    rng = random.Random(seed)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=A4)
    elements = [
        Paragraph("FARO Import-Export GmbH", styles["Title"]),
        Paragraph(f"Invoice #{rng.randint(10000, 99999)}", styles["Normal"]),
        Spacer(1, 12),
    ]

    data = [["Article", "Qty", "Unit Price (EUR)"]]
    for _ in range(n_line_items):
        data.append([
            rng.choice(ARTICLE_NAMES),
            str(rng.randint(1, 100)),
            f"{rng.uniform(2.5, 150.0):.2f}",
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Payment due within 30 days. Thank you for your business.", styles["Normal"]))

    doc.build(elements)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_synthetic_data.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
cd "I:/docs/local-llm-pipeline"
git add requirements.txt README.md scripts tests
git commit -m "chore: scaffold project, add synthetic data generator"
```

---

### Task 2: Tabular extractor (CSV/XLSX) — header detection, cleaning, FACTS block

**Files:**
- Create: `local-llm-pipeline/src/__init__.py`
- Create: `local-llm-pipeline/src/extractors/__init__.py`
- Create: `local-llm-pipeline/src/extractors/tabular.py`
- Test: `local-llm-pipeline/tests/test_tabular.py`

**Interfaces:**
- Consumes: `generate_messy_inventory_xlsx` from Task 1 (test fixture only).
- Produces: `TabularExtraction` dataclass with fields `markdown_table: str`, `facts: dict`, `dataframe: pandas.DataFrame`.
- Produces: `extract_tabular(path: str) -> TabularExtraction` — used by Task 4 (router) and Task 5 (tools).

- [ ] **Step 1: Write the failing tests**

Create `local-llm-pipeline/tests/test_tabular.py`:

```python
import pandas as pd
from src.extractors.tabular import extract_tabular
from scripts.generate_synthetic_data import generate_messy_inventory_xlsx


def test_extract_tabular_skips_title_and_blank_rows(tmp_path):
    path = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(path), n_rows=40, seed=2)

    result = extract_tabular(str(path))

    assert list(result.dataframe.columns) == ["Article", "Quantity", "Unit Price EUR", "Supplier"]
    assert result.facts["row_count"] == 40


def test_extract_tabular_facts_are_exact(tmp_path):
    path = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(path), n_rows=40, seed=2)

    result = extract_tabular(str(path))
    expected_sum = pd.to_numeric(result.dataframe["Quantity"], errors="coerce").sum()

    assert result.facts["columns"]["Quantity"]["sum"] == expected_sum
    assert result.facts["columns"]["Quantity"]["unique_count"] == result.dataframe["Quantity"].nunique()


def test_extract_tabular_markdown_contains_header(tmp_path):
    path = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(path), n_rows=5, seed=3)

    result = extract_tabular(str(path))

    assert "Article" in result.markdown_table
    assert "FARO Inventory Export" not in result.markdown_table
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_tabular.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src'`

- [ ] **Step 3: Implement the tabular extractor**

Create `local-llm-pipeline/src/extractors/tabular.py`:

```python
from dataclasses import dataclass

import pandas as pd


@dataclass
class TabularExtraction:
    markdown_table: str
    facts: dict
    dataframe: pd.DataFrame


def _detect_header_row(raw: pd.DataFrame, max_scan: int = 10) -> int:
    best_row = 0
    best_score = -1
    for i in range(min(max_scan, len(raw))):
        row = raw.iloc[i]
        non_null = row.notna().sum()
        non_numeric_strings = sum(
            isinstance(v, str) and not v.strip().replace(".", "", 1).isdigit()
            for v in row
            if pd.notna(v)
        )
        score = non_null + non_numeric_strings
        if score > best_score:
            best_score = score
            best_row = i
    return best_row


def _load_raw(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        return pd.read_csv(path, header=None)
    return pd.read_excel(path, header=None)


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


def extract_tabular(path: str) -> TabularExtraction:
    raw = _load_raw(path)
    header_row = _detect_header_row(raw)

    header = raw.iloc[header_row]
    df = raw.iloc[header_row + 1:].copy()
    df.columns = [str(c) for c in header]
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    df = df.reset_index(drop=True)

    facts = _compute_facts(df)
    markdown_table = df.to_markdown(index=False)

    return TabularExtraction(markdown_table=markdown_table, facts=facts, dataframe=df)
```

Create empty `local-llm-pipeline/src/__init__.py` and `local-llm-pipeline/src/extractors/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_tabular.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd "I:/docs/local-llm-pipeline"
git add src tests/test_tabular.py
git commit -m "feat: add tabular extractor with header detection and FACTS block"
```

---

### Task 3: PDF extractor — text/table extraction with OCR fallback

**Files:**
- Create: `local-llm-pipeline/src/extractors/pdf.py`
- Test: `local-llm-pipeline/tests/test_pdf.py`

**Interfaces:**
- Consumes: `generate_invoice_pdf` from Task 1 (test fixture only).
- Produces: `PDFPage` dataclass (`page_number: int`, `text: str`, `used_ocr: bool`, `tables_markdown: list[str]`).
- Produces: `PDFExtraction` dataclass (`pages: list[PDFPage]`, `full_text: str`).
- Produces: `extract_pdf(path: str) -> PDFExtraction` — used by Task 4 (router).

- [ ] **Step 1: Write the failing tests**

Create `local-llm-pipeline/tests/test_pdf.py`:

```python
from src.extractors.pdf import extract_pdf
from scripts.generate_synthetic_data import generate_invoice_pdf


def test_extract_pdf_reads_text_without_ocr(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=25, seed=4)

    result = extract_pdf(str(path))

    assert len(result.pages) >= 1
    assert result.pages[0].used_ocr is False
    assert "FARO Import-Export" in result.full_text


def test_extract_pdf_finds_table(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=25, seed=4)

    result = extract_pdf(str(path))

    all_tables = [t for p in result.pages for t in p.tables_markdown]
    assert len(all_tables) >= 1
    assert "Article" in all_tables[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_pdf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.extractors.pdf'`

- [ ] **Step 3: Implement the PDF extractor**

Create `local-llm-pipeline/src/extractors/pdf.py`:

```python
from dataclasses import dataclass

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io

OCR_TEXT_THRESHOLD = 20  # chars; below this, treat page as image-only


@dataclass
class PDFPage:
    page_number: int
    text: str
    used_ocr: bool
    tables_markdown: list[str]


@dataclass
class PDFExtraction:
    pages: list[PDFPage]
    full_text: str


def _table_to_markdown(table) -> str:
    rows = table.extract()
    if not rows:
        return ""
    header, *body = rows
    header_line = "| " + " | ".join(str(c or "") for c in header) + " |"
    sep_line = "| " + " | ".join("---" for _ in header) + " |"
    body_lines = [
        "| " + " | ".join(str(c or "") for c in row) + " |"
        for row in body
    ]
    return "\n".join([header_line, sep_line, *body_lines])


def _ocr_page(page) -> str:
    pix = page.get_pixmap(dpi=200)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(image)


def extract_pdf(path: str) -> PDFExtraction:
    doc = fitz.open(path)
    pages: list[PDFPage] = []

    for i, page in enumerate(doc):
        text = page.get_text()
        used_ocr = False
        if len(text.strip()) < OCR_TEXT_THRESHOLD:
            text = _ocr_page(page)
            used_ocr = True

        found = page.find_tables()
        tables_markdown = [_table_to_markdown(t) for t in found.tables]

        pages.append(PDFPage(
            page_number=i + 1,
            text=text,
            used_ocr=used_ocr,
            tables_markdown=tables_markdown,
        ))

    full_text = "\n\n".join(p.text for p in pages)
    return PDFExtraction(pages=pages, full_text=full_text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_pdf.py -v`
Expected: PASS (2 tests)

If `find_tables` reports zero tables for the reportlab-generated PDF (table
detection heuristics vary by PyMuPDF version), lower `OCR_TEXT_THRESHOLD`
is not the fix — instead verify the installed `pymupdf` version supports
`Page.find_tables()` (added in 1.23+); the pinned `pymupdf>=1.24` in
`requirements.txt` should be sufficient.

- [ ] **Step 5: Commit**

```bash
cd "I:/docs/local-llm-pipeline"
git add src/extractors/pdf.py tests/test_pdf.py
git commit -m "feat: add PDF extractor with table detection and OCR fallback"
```

---

### Task 4: File-type router with explicit unparseable-file handling

**Files:**
- Create: `local-llm-pipeline/src/extractors/router.py`
- Test: `local-llm-pipeline/tests/test_router.py`

**Interfaces:**
- Consumes: `extract_tabular` (Task 2), `extract_pdf` (Task 3).
- Produces: `ExtractionResult` dataclass (`kind: str`, `markdown: str | None`, `facts: dict | None`, `dataframe: "pandas.DataFrame | None"`, `parse_failed: bool`, `message: str | None`).
- Produces: `extract_document(path: str) -> ExtractionResult` — used by Task 5 and Task 6.

- [ ] **Step 1: Write the failing tests**

Create `local-llm-pipeline/tests/test_router.py`:

```python
from src.extractors.router import extract_document
from scripts.generate_synthetic_data import generate_messy_inventory_xlsx, generate_invoice_pdf


def test_router_handles_xlsx(tmp_path):
    path = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(path), n_rows=10, seed=5)

    result = extract_document(str(path))

    assert result.kind == "tabular"
    assert result.parse_failed is False
    assert result.facts["row_count"] == 10


def test_router_handles_pdf(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=10, seed=5)

    result = extract_document(str(path))

    assert result.kind == "pdf"
    assert result.parse_failed is False
    assert "FARO" in result.markdown


def test_router_reports_unparseable_file(tmp_path):
    path = tmp_path / "mystery.xyz"
    path.write_bytes(b"\x00\x01\x02 not a real document")

    result = extract_document(str(path))

    assert result.parse_failed is True
    assert result.message is not None
    assert "couldn't" in result.message.lower() or "could not" in result.message.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.extractors.router'`

- [ ] **Step 3: Implement the router**

Create `local-llm-pipeline/src/extractors/router.py`:

```python
from dataclasses import dataclass

import pandas as pd

from src.extractors.tabular import extract_tabular
from src.extractors.pdf import extract_pdf

TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


@dataclass
class ExtractionResult:
    kind: str
    markdown: str | None
    facts: dict | None
    dataframe: pd.DataFrame | None
    parse_failed: bool
    message: str | None


def extract_document(path: str) -> ExtractionResult:
    ext = path.lower().rsplit(".", 1)[-1]

    if ext in TABULAR_EXTENSIONS:
        result = extract_tabular(path)
        return ExtractionResult(
            kind="tabular",
            markdown=result.markdown_table,
            facts=result.facts,
            dataframe=result.dataframe,
            parse_failed=False,
            message=None,
        )

    if ext == "pdf":
        result = extract_pdf(path)
        table_blocks = "\n\n".join(
            t for page in result.pages for t in page.tables_markdown
        )
        markdown = result.full_text + ("\n\n" + table_blocks if table_blocks else "")
        return ExtractionResult(
            kind="pdf",
            markdown=markdown,
            facts={"page_count": len(result.pages)},
            dataframe=None,
            parse_failed=False,
            message=None,
        )

    return ExtractionResult(
        kind="unknown",
        markdown=None,
        facts=None,
        dataframe=None,
        parse_failed=True,
        message=f"Couldn't fully parse this file (unsupported type: .{ext}).",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_router.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd "I:/docs/local-llm-pipeline"
git add src/extractors/router.py tests/test_router.py
git commit -m "feat: add file-type router with explicit unparseable-file handling"
```

---

### Task 5: Callable tools backed by the extracted DataFrame

**Files:**
- Create: `local-llm-pipeline/src/tools.py`
- Test: `local-llm-pipeline/tests/test_tools.py`

**Interfaces:**
- Consumes: `pandas.DataFrame` (as produced by `extract_tabular`/`extract_document`).
- Produces: `count_rows(df, filter_expr: str | None = None) -> int`
- Produces: `sum_column(df, column: str, filter_expr: str | None = None) -> float`
- Produces: `get_row(df, index: int) -> dict`
- Produces: `TOOL_SCHEMAS: list[dict]` (Ollama/OpenAI-style function schemas) — used by Task 6.

- [ ] **Step 1: Write the failing tests**

Create `local-llm-pipeline/tests/test_tools.py`:

```python
import pandas as pd
import pytest
from src.tools import count_rows, sum_column, get_row


@pytest.fixture
def df():
    return pd.DataFrame({
        "Article": ["Screen", "Battery", "Case", "Screen"],
        "Quantity": [10, 5, 20, 3],
        "Unit Price EUR": [50.0, 20.0, 5.0, 50.0],
    })


def test_count_rows_no_filter(df):
    assert count_rows(df) == 4


def test_count_rows_with_filter(df):
    assert count_rows(df, "`Unit Price EUR` > 10") == 3


def test_sum_column_no_filter(df):
    assert sum_column(df, "Quantity") == 38


def test_sum_column_with_filter(df):
    assert sum_column(df, "Quantity", "Article == 'Screen'") == 13


def test_get_row(df):
    row = get_row(df, 1)
    assert row["Article"] == "Battery"
    assert row["Quantity"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.tools'`

- [ ] **Step 3: Implement the tools**

Create `local-llm-pipeline/src/tools.py`:

```python
import pandas as pd


def _apply_filter(df: pd.DataFrame, filter_expr: str | None) -> pd.DataFrame:
    if not filter_expr:
        return df
    return df.query(filter_expr)


def count_rows(df: pd.DataFrame, filter_expr: str | None = None) -> int:
    return len(_apply_filter(df, filter_expr))


def sum_column(df: pd.DataFrame, column: str, filter_expr: str | None = None) -> float:
    subset = _apply_filter(df, filter_expr)
    return float(pd.to_numeric(subset[column], errors="coerce").sum())


def get_row(df: pd.DataFrame, index: int) -> dict:
    return df.iloc[index].to_dict()


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "count_rows",
            "description": "Count rows in the uploaded table. Optionally filter first using a pandas query expression (column names with spaces need backticks, e.g. `Unit Price EUR` > 10).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_expr": {"type": "string", "description": "Optional pandas query expression"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sum_column",
            "description": "Sum a numeric column in the uploaded table, optionally filtered by a pandas query expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "column": {"type": "string", "description": "Exact column name to sum"},
                    "filter_expr": {"type": "string", "description": "Optional pandas query expression"},
                },
                "required": ["column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_row",
            "description": "Get a single row from the uploaded table by its zero-based index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Zero-based row index"}
                },
                "required": ["index"],
            },
        },
    },
]
```

Note for later hardening (not needed for this prototype): `df.query()`
evaluates an expression string against the DataFrame's own columns and is
scoped to this in-memory table per upload, but before this ships past
Phase 0, restrict `filter_expr` to a whitelist of the DataFrame's actual
column names and comparison operators rather than passing the LLM's
string straight through.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_tools.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd "I:/docs/local-llm-pipeline"
git add src/tools.py tests/test_tools.py
git commit -m "feat: add count_rows/sum_column/get_row tools with schemas"
```

---

### Task 6: Ollama tool-calling agent + end-to-end validation

**Files:**
- Create: `local-llm-pipeline/src/agent.py`
- Test: `local-llm-pipeline/tests/test_agent_e2e.py`

**Interfaces:**
- Consumes: `ExtractionResult` (Task 4), `TOOL_SCHEMAS`, `count_rows`, `sum_column`, `get_row` (Task 5).
- Produces: `answer_question(model: str, extraction: ExtractionResult, question: str) -> str` — this is the function a future Open WebUI Pipeline (Phase 1) will call per user message.

- [ ] **Step 1: Write the failing end-to-end test**

Create `local-llm-pipeline/tests/test_agent_e2e.py`:

```python
import shutil

import pytest

from src.agent import answer_question
from src.extractors.router import extract_document
from scripts.generate_synthetic_data import generate_invoice_pdf

OLLAMA_MODEL = "qwen2.5:7b"

requires_ollama = pytest.mark.skipif(
    shutil.which("ollama") is None,
    reason="ollama not installed/available on this machine",
)


@requires_ollama
def test_agent_counts_line_items_exactly(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=17, seed=9)

    extraction = extract_document(str(path))
    answer = answer_question(OLLAMA_MODEL, extraction, "How many line items are on this invoice? Answer with just the number.")

    assert "17" in answer
```

- [ ] **Step 2: Run test to verify it fails (or skips)**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_agent_e2e.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent'` if `ollama` CLI is present, or SKIPPED if it isn't. Either is fine at this step — implement next.

- [ ] **Step 3: Implement the agent**

Create `local-llm-pipeline/src/agent.py`:

```python
import json

import ollama

from src.extractors.router import ExtractionResult
from src.tools import TOOL_SCHEMAS, count_rows, sum_column, get_row

TOOL_IMPLEMENTATIONS = {
    "count_rows": count_rows,
    "sum_column": sum_column,
    "get_row": get_row,
}

SYSTEM_PROMPT = (
    "You answer questions about one uploaded business document (an invoice "
    "or inventory list). The document's cleaned content is provided below. "
    "For any question involving counting, summing, or totals, you MUST call "
    "the matching tool rather than counting or adding numbers yourself — "
    "the tools compute exact values from the real data; your own counting "
    "over text is not reliable enough for this task."
)


def _build_messages(extraction: ExtractionResult, question: str) -> list[dict]:
    context = extraction.markdown or ""
    if extraction.facts:
        context += f"\n\nFACTS: {json.dumps(extraction.facts)}"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"DOCUMENT:\n{context}\n\nQUESTION: {question}"},
    ]


def answer_question(model: str, extraction: ExtractionResult, question: str) -> str:
    if extraction.parse_failed:
        return extraction.message or "Couldn't parse this document."

    messages = _build_messages(extraction, question)
    df = extraction.dataframe

    for _ in range(4):  # bounded tool-call loop
        response = ollama.chat(model=model, messages=messages, tools=TOOL_SCHEMAS)
        message = response["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return message["content"]

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if name == "count_rows":
                result = count_rows(df, args.get("filter_expr"))
            elif name == "sum_column":
                result = sum_column(df, args["column"], args.get("filter_expr"))
            elif name == "get_row":
                result = get_row(df, args["index"])
            else:
                result = f"unknown tool: {name}"

            messages.append({
                "role": "tool",
                "content": json.dumps(result),
                "name": name,
            })

    return "Couldn't reach a final answer within the tool-call budget."
```

- [ ] **Step 4: Run test to verify it passes (or skips cleanly)**

Run: `cd "I:/docs/local-llm-pipeline" && pytest tests/test_agent_e2e.py -v`
Expected: PASS if Ollama + `qwen2.5:7b` (or another tool-calling-capable
model — adjust `OLLAMA_MODEL` in the test to whatever is pulled locally)
are available; SKIPPED otherwise. If it runs but fails, inspect the
printed conversation (`messages`) — this is the real integration point
worth debugging by hand, since it depends on the specific local model's
tool-calling reliability, not just this code.

- [ ] **Step 5: Run the full test suite**

Run: `cd "I:/docs/local-llm-pipeline" && pytest -v`
Expected: All tests PASS (agent e2e test PASS or SKIP depending on local Ollama availability).

- [ ] **Step 6: Commit**

```bash
cd "I:/docs/local-llm-pipeline"
git add src/agent.py tests/test_agent_e2e.py
git commit -m "feat: add Ollama tool-calling agent with end-to-end test"
```

---

## Self-Review Notes

- **Spec coverage:** §5 (router + extractors) → Tasks 2–4; §6 (tool-calling for exact counts) → Tasks 5–6; §5's "explicit failure, no silent guessing" → Task 4's `parse_failed`/`message` fields; §8 Phase 0 ("build/test on personal PC, no Mac Studio needed") → the whole plan's Global Constraints. DOCX extraction (mentioned in spec §5) and Phase 1 Open WebUI packaging are intentionally **not** in this plan — Phase 0 scope is PDF + CSV/XLSX + tool-calling validation only; DOCX and packaging are follow-up plans once this is validated.
- **Type consistency:** `ExtractionResult.dataframe` (Task 4) matches the `df` parameter type used by `tools.py` (Task 5) and `agent.py` (Task 6); `TOOL_SCHEMAS` names (`count_rows`, `sum_column`, `get_row`) match both the `TOOL_IMPLEMENTATIONS` dispatch in `agent.py` and the actual function names in `tools.py`.
- **Known follow-up (not blocking Phase 0):** harden `filter_expr` handling in `tools.py` before Phase 1; add a DOCX extractor task; add the Open WebUI Pipeline wrapper as its own plan once this core logic is validated against real-shaped data.
