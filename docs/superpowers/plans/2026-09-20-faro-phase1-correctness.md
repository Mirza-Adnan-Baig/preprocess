# FARO Phase 1 — Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate every known silent-wrong-answer path for German documents, behind a tested core library that generates the single file pasted into Open WebUI.

**Architecture:** A new package `src/faro_docs/` holds all document logic: German-aware parsing, table discovery, deterministic facts, and the question-answering loop. A thin adapter in `adapters/openwebui_pipe.py` turns Open WebUI's `__files__` into a streamed answer. `tools/build_bundle.py` flattens core plus adapter into one dependency-free file at `openwebui/faro_document_assistant.py`, which replaces the hand-edited `exact_count_pipe.py`. Tests run against the library; the bundle is generated, never edited.

**Tech Stack:** Python 3.12, pandas, openpyxl, xlrd, PyMuPDF, charset_normalizer, ftfy, tabulate, ollama, pydantic — all already present in Open WebUI's environment. pytest and reportlab are dev-only.

**Spec:** `docs/design-spec-v2-universal.md` (also at `I:\docs\superpowers\specs\2026-09-20-faro-universal-document-understanding-design.md`)

## Global Constraints

- **The generated bundle must declare no pip requirements.** Only these imports are permitted in shipped code: stdlib, `pandas`, `numpy`, `openpyxl`, `xlrd`, `fitz` (PyMuPDF), `PIL`, `docx`, `pptx`, `bs4`, `lxml`, `chardet`, `charset_normalizer`, `ftfy`, `tabulate`, `ollama`, `pydantic`. Enforced by a test.
- **All user-facing text is German.** Every string a non-technical user can see lives in `messages_de.py`. No English in errors, status lines, or the honesty footer.
- **Never emit a number that did not come from code.** Counts, sums and totals come from tools or precomputed facts, never from the model's own arithmetic.
- **Numeric format is decided per column, never per cell**, and the decision is recorded and reportable.
- **Totals rows are excluded from counts and sums by default**, and the exclusion is stated.
- **All uploaded files are processed**, not just the first.
- **The pipe is a plain synchronous generator**, never `async` (open-webui#20196), with a heartbeat during Ollama prefill.
- Tolerance for totals-row matching: within 0.5 % or 0,02 absolute, whichever is larger.
- Test imports use the existing repo convention: `from src.faro_docs.<module> import ...`.

---

### Task 1: Package skeleton and shared types

**Files:**
- Create: `src/faro_docs/__init__.py`
- Create: `src/faro_docs/model.py`
- Create: `pytest.ini`
- Create: `tests/faro_docs/__init__.py`
- Test: `tests/faro_docs/test_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ColumnInfo(name: str, numeric_style: str, numeric_rule: str, numeric_confident: bool)`, `Table(id: str, label: str, frame: pd.DataFrame, columns: dict[str, ColumnInfo], totals_rows: list[dict], notes: list[str])`, `Document(id: str, filename: str, media_type: str, text: str, tables: list[Table], notes: list[str])`. Every later task uses these.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_model.py
import pandas as pd

from src.faro_docs.model import ColumnInfo, Document, Table


def test_table_defaults_are_independent_between_instances():
    a = Table(id="dok1:tabelle1", label="Tabelle 1", frame=pd.DataFrame({"Menge": [1]}))
    b = Table(id="dok1:tabelle2", label="Tabelle 2", frame=pd.DataFrame({"Menge": [2]}))
    a.notes.append("Summenzeile ausgeschlossen")
    assert b.notes == []
    assert a.columns == {} and b.totals_rows == []


def test_document_row_total_sums_its_tables():
    doc = Document(
        id="dok1",
        filename="rechnung.pdf",
        media_type="application/pdf",
        text="",
        tables=[
            Table(id="dok1:t1", label="T1", frame=pd.DataFrame({"a": [1, 2, 3]})),
            Table(id="dok1:t2", label="T2", frame=pd.DataFrame({"a": [1, 2]})),
        ],
    )
    assert doc.total_rows() == 5


def test_column_info_records_its_decision():
    info = ColumnInfo(
        name="Betrag",
        numeric_style="german",
        numeric_rule="Dezimalkomma erkannt",
        numeric_confident=True,
    )
    assert info.numeric_style == "german"
    assert info.numeric_rule
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.faro_docs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/faro_docs/__init__.py
"""Core document-understanding library for the FARO local LLM pipeline."""
```

```python
# src/faro_docs/model.py
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ColumnInfo:
    """How one column was interpreted, so any answer derived from it can be explained."""

    name: str
    numeric_style: str = "none"  # "german" | "english" | "integer" | "none"
    numeric_rule: str = ""  # German explanation of why this style was chosen
    numeric_confident: bool = True


@dataclass
class Table:
    id: str
    label: str
    frame: pd.DataFrame
    columns: dict[str, ColumnInfo] = field(default_factory=dict)
    totals_rows: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def row_count(self) -> int:
        return len(self.frame)


@dataclass
class Document:
    id: str
    filename: str
    media_type: str
    text: str = ""
    tables: list[Table] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def total_rows(self) -> int:
        return sum(table.row_count() for table in self.tables)
```

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q
```

Create `tests/faro_docs/__init__.py` as an empty file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_model.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/__init__.py src/faro_docs/model.py pytest.ini tests/faro_docs/__init__.py tests/faro_docs/test_model.py
git commit -m "feat: add faro_docs core package skeleton and shared types"
```

---

### Task 2: German numeric format detection and parsing

This is the task that fixes the proven bug: `1.234 / 2.500 / 750` currently sums to 753,734 instead of 4.484.

**Files:**
- Create: `src/faro_docs/german.py`
- Test: `tests/faro_docs/test_german_numbers.py`

**Interfaces:**
- Consumes: `ColumnInfo` from Task 1.
- Produces: `detect_numeric_format(values: Iterable[object], fallback_style: str = "german") -> tuple[str, str, bool]` returning `(style, german_rule_text, confident)`; `parse_number(value: object, style: str) -> float | None`; `to_numeric_series(series: pd.Series, style: str) -> pd.Series`; `document_numeric_fallback(columns: Iterable[Iterable[object]]) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_german_numbers.py
import pandas as pd
import pytest

from src.faro_docs.german import (
    detect_numeric_format,
    document_numeric_fallback,
    parse_number,
    to_numeric_series,
)


class TestDetection:
    def test_decimal_comma_is_german(self):
        style, rule, confident = detect_numeric_format(["12,00", "0,5", "149,82"])
        assert style == "german"
        assert confident
        assert rule  # German explanation present

    def test_thousands_dot_with_decimal_comma_is_german(self):
        style, _, confident = detect_numeric_format(["1.234,56", "999,00"])
        assert style == "german"
        assert confident

    def test_thousands_comma_with_decimal_point_is_english(self):
        style, _, confident = detect_numeric_format(["1,234.56", "999.00"])
        assert style == "english"
        assert confident

    def test_two_decimal_places_after_dot_is_english(self):
        style, _, _ = detect_numeric_format(["12.00", "149.82"])
        assert style == "english"

    def test_bare_thousands_dot_defaults_to_german(self):
        # The proven bug: these are 1234 / 2500, not 1.234 / 2.5
        style, _, confident = detect_numeric_format(["1.234", "2.500", "750"])
        assert style == "german"
        assert confident is False  # ambiguous, decision must be recorded

    def test_bare_thousands_dot_reads_english_when_document_says_so(self):
        style, _, _ = detect_numeric_format(["1.234", "2.500"], fallback_style="english")
        assert style == "english"

    def test_plain_integers_are_integer(self):
        style, _, _ = detect_numeric_format(["1", "42", "750"])
        assert style == "integer"

    def test_non_numeric_column_is_none(self):
        style, _, _ = detect_numeric_format(["iPhone Display", "USB-C Kabel"])
        assert style == "none"

    def test_empty_column_is_none(self):
        style, _, _ = detect_numeric_format([None, "", "   "])
        assert style == "none"

    def test_mixed_evidence_is_not_confident(self):
        style, _, confident = detect_numeric_format(["1.234,56", "1,234.56"])
        assert style in {"german", "english"}
        assert confident is False

    def test_currency_and_whitespace_are_ignored(self):
        style, _, _ = detect_numeric_format(["1.234,56 €", "  EUR 99,00"])
        assert style == "german"


class TestParsing:
    @pytest.mark.parametrize(
        "text,style,expected",
        [
            ("1.234,56", "german", 1234.56),
            ("12,00", "german", 12.0),
            ("0,5", "german", 0.5),
            ("1.234", "german", 1234.0),
            ("1 234,56", "german", 1234.56),
            ("1\u00a0234,56", "german", 1234.56),
            ("1,234.56", "english", 1234.56),
            ("1.234", "english", 1.234),
            ("750", "integer", 750.0),
            ("1.234,56 €", "german", 1234.56),
            ("-1.234,56", "german", -1234.56),
            ("1.234,56-", "german", -1234.56),
            ("(1.234,56)", "german", -1234.56),
        ],
    )
    def test_parses_value(self, text, style, expected):
        assert parse_number(text, style) == pytest.approx(expected)

    def test_unparseable_returns_none(self):
        assert parse_number("keine Zahl", "german") is None
        assert parse_number(None, "german") is None

    def test_passes_through_real_numbers(self):
        assert parse_number(42, "german") == 42.0


class TestSeries:
    def test_german_series_sums_correctly(self):
        # The regression that motivated this whole task
        series = pd.Series(["1.234", "2.500", "750"])
        assert to_numeric_series(series, "german").sum() == pytest.approx(4484.0)

    def test_german_decimals_are_not_dropped(self):
        series = pd.Series(["12,00", "8,50"])
        result = to_numeric_series(series, "german")
        assert result.notna().all()
        assert result.sum() == pytest.approx(20.5)


class TestDocumentFallback:
    def test_english_evidence_anywhere_sets_english_fallback(self):
        assert document_numeric_fallback([["1,234.56"], ["1.234"]]) == "english"

    def test_german_is_the_default(self):
        assert document_numeric_fallback([["Artikel"], ["1.234"]]) == "german"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_german_numbers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.faro_docs.german'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/faro_docs/german.py
"""German-first parsing: numbers, encodings, delimiters.

Every user and every document is German. German writes 1.234,56 where English
writes 1,234.56, which means a naive pd.to_numeric turns 1.234 into the float
1.234 and throws away 12,00 as "not a number". Both failures are silent and
produce confidently wrong totals, so format is decided once per column from all
its values -- never per cell -- and the decision is recorded so any number can
be explained afterwards.
"""

import re
from collections.abc import Iterable

import pandas as pd

_SPACES = "\u00a0\u202f\u2009 "
_CURRENCY = re.compile(r"(?i)(?:€|eur|chf)")

# Exactly three digits per group is what makes a dot a thousands separator.
_GERMAN_GROUPED_DECIMAL = re.compile(rf"^\d{{1,3}}(?:[.{_SPACES}]\d{{3}})+,\d+$")
_GERMAN_DECIMAL = re.compile(r"^\d+,\d+$")
_ENGLISH_GROUPED_DECIMAL = re.compile(r"^\d{1,3}(?:,\d{3})+\.\d+$")
_ENGLISH_DECIMAL = re.compile(r"^\d+\.\d{1,2}$|^\d+\.\d{4,}$")
_AMBIGUOUS_DOTTED = re.compile(rf"^\d{{1,3}}(?:[.{_SPACES}]\d{{3}})+$")
_ENGLISH_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_PLAIN_INTEGER = re.compile(r"^\d+$")

RULE_DECIMAL_COMMA = "Dezimalkomma erkannt (deutsches Format)"
RULE_DECIMAL_POINT = "Dezimalpunkt erkannt (englisches Format)"
RULE_AMBIGUOUS_GERMAN = (
    "Punkt als Tausendertrennzeichen gedeutet (deutsches Format); "
    "eindeutig ist es nicht"
)
RULE_AMBIGUOUS_ENGLISH = (
    "Punkt als Dezimaltrennzeichen gedeutet (englisches Format); "
    "eindeutig ist es nicht"
)
RULE_MIXED = "Gemischte Zahlenformate in derselben Spalte"
RULE_INTEGER = "Ganze Zahlen ohne Trennzeichen"
RULE_NONE = "Keine Zahlenspalte"


def _clean(value: object) -> str:
    """Strip currency, whitespace and negative notation, keeping digits and separators."""
    if value is None or isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    text = _CURRENCY.sub("", text).strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    text = text.lstrip("+-").rstrip("-").strip()
    return text


def _is_negative(value: object) -> bool:
    text = str(value).strip()
    return text.startswith("-") or text.endswith("-") or (
        text.startswith("(") and text.endswith(")")
    )


def detect_numeric_format(
    values: Iterable[object], fallback_style: str = "german"
) -> tuple[str, str, bool]:
    """Decide one numeric format for a whole column.

    Returns (style, german_rule_text, confident). Style is one of
    "german", "english", "integer", "none".
    """
    cleaned = [_clean(v) for v in values]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return "none", RULE_NONE, True

    german = sum(
        bool(_GERMAN_GROUPED_DECIMAL.match(c) or _GERMAN_DECIMAL.match(c))
        for c in cleaned
    )
    english = sum(
        bool(_ENGLISH_GROUPED_DECIMAL.match(c) or _ENGLISH_DECIMAL.match(c))
        for c in cleaned
    )
    ambiguous = sum(bool(_AMBIGUOUS_DOTTED.match(c)) for c in cleaned)
    english_grouped = sum(bool(_ENGLISH_GROUPED.match(c)) for c in cleaned)
    plain = sum(bool(_PLAIN_INTEGER.match(c)) for c in cleaned)

    recognised = german + english + ambiguous + english_grouped + plain
    if recognised == 0:
        return "none", RULE_NONE, True

    if german and english:
        winner = "german" if german >= english else "english"
        return winner, RULE_MIXED, False
    if german:
        return "german", RULE_DECIMAL_COMMA, True
    if english:
        return "english", RULE_DECIMAL_POINT, True
    if english_grouped:
        return "english", RULE_DECIMAL_POINT, True
    if ambiguous:
        # 1.234 with nothing else to go on. Spec §7 resolution order (c):
        # default German unless the document says otherwise.
        if fallback_style == "english":
            return "english", RULE_AMBIGUOUS_ENGLISH, False
        return "german", RULE_AMBIGUOUS_GERMAN, False
    return "integer", RULE_INTEGER, True


def document_numeric_fallback(columns: Iterable[Iterable[object]]) -> str:
    """Scan a whole document for unambiguous English formatting.

    Used to resolve columns that are ambiguous on their own (spec §7 rule b).
    """
    for column in columns:
        for value in column:
            cleaned = _clean(value)
            if cleaned and (
                _ENGLISH_GROUPED_DECIMAL.match(cleaned) or _ENGLISH_DECIMAL.match(cleaned)
            ):
                return "english"
    return "german"


def parse_number(value: object, style: str) -> float | None:
    """Parse one value using an already-decided column style."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if pd.isna(value):
            return None
        return float(value)

    cleaned = _clean(value)
    if not cleaned:
        return None

    negative = _is_negative(value)

    if style == "german":
        normalised = cleaned
        for space in _SPACES:
            normalised = normalised.replace(space, "")
        normalised = normalised.replace(".", "").replace(",", ".")
    elif style == "english":
        normalised = cleaned.replace(",", "")
    elif style == "integer":
        normalised = cleaned
        for space in _SPACES:
            normalised = normalised.replace(space, "")
        normalised = normalised.replace(".", "").replace(",", "")
    else:
        return None

    try:
        result = float(normalised)
    except ValueError:
        return None
    return -result if negative else result


def to_numeric_series(series: pd.Series, style: str) -> pd.Series:
    """Convert a column to real numbers using its decided style."""
    if style == "none":
        return pd.Series([None] * len(series), index=series.index, dtype="float64")
    return pd.Series(
        [parse_number(v, style) for v in series], index=series.index, dtype="float64"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_german_numbers.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/german.py tests/faro_docs/test_german_numbers.py
git commit -m "fix: decide numeric format per column so German numbers parse correctly"
```

---

### Task 3: German encoding and delimiter detection

**Files:**
- Modify: `src/faro_docs/german.py`
- Test: `tests/faro_docs/test_german_text.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `decode_text(raw: bytes) -> tuple[str, str]` returning `(text, encoding_name)`; `detect_delimiter(text: str) -> str`; `fold(value: str) -> str` (diacritic- and case-insensitive key used by header and totals matching).

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_german_text.py
from src.faro_docs.german import decode_text, detect_delimiter, fold


class TestDecoding:
    def test_reads_utf8_with_bom(self):
        text, encoding = decode_text("Artikel;Menge\nStraße;3\n".encode("utf-8-sig"))
        assert "Straße" in text
        assert "utf-8" in encoding.lower()

    def test_reads_cp1252_umlauts(self):
        text, _ = decode_text("Artikel;Größe\nHülle;2\n".encode("cp1252"))
        assert "Größe" in text
        assert "Hülle" in text

    def test_repairs_mojibake(self):
        # cp1252 bytes mistakenly decoded as latin-1 upstream produce StraÃŸe
        broken = "StraÃŸe".encode("utf-8")
        text, _ = decode_text(broken)
        assert "Straße" in text

    def test_never_raises_on_binary_garbage(self):
        text, _ = decode_text(b"\xff\xfe\x00\x01\x02")
        assert isinstance(text, str)


class TestDelimiter:
    def test_detects_german_semicolon(self):
        assert detect_delimiter("Artikel;Menge;Preis\nHülle;3;12,00\n") == ";"

    def test_detects_comma(self):
        assert detect_delimiter("Article,Qty,Price\nCase,3,12.00\n") == ","

    def test_detects_tab(self):
        assert detect_delimiter("Artikel\tMenge\nHülle\t3\n") == "\t"

    def test_ignores_preamble_junk(self):
        text = "Rechnung Nr. 4711\nKunde: Müller GmbH\n\nArtikel;Menge;Preis\nHülle;3;12,00\nKabel;5;8,50\n"
        assert detect_delimiter(text) == ";"

    def test_semicolon_wins_when_decimal_commas_present(self):
        # "12,00" must not make this look comma-delimited
        assert detect_delimiter("Artikel;Preis\nHülle;12,00\nKabel;8,50\n") == ";"

    def test_defaults_to_semicolon_for_single_column(self):
        assert detect_delimiter("Artikel\nHülle\n") in {";", ","}


class TestFold:
    def test_folds_case_and_umlauts(self):
        assert fold("Stückzahl") == fold("STUECKZAHL") == fold("stuckzahl")

    def test_strips_punctuation_and_space(self):
        assert fold(" Pos. ") == fold("pos")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_german_text.py -v`
Expected: FAIL with `ImportError: cannot import name 'decode_text'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/faro_docs/german.py`:

```python
import unicodedata

_UMLAUT_MAP = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"}
)


def decode_text(raw: bytes) -> tuple[str, str]:
    """Decode uploaded bytes to text, repairing German mojibake.

    German CSV exports are frequently cp1252, and text that has already been
    decoded wrongly upstream arrives as StraÃŸe rather than Straße. ftfy fixes
    the second case; it is bundled with Open WebUI so it costs no dependency.
    """
    text = None
    encoding = "utf-8"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            from charset_normalizer import from_bytes

            best = from_bytes(raw).best()
            if best is not None:
                text = str(best)
                encoding = best.encoding
        except Exception:
            text = None
    if text is None:
        try:
            text = raw.decode("cp1252")
            encoding = "cp1252"
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            encoding = "utf-8 (mit Ersatzzeichen)"

    try:
        from ftfy import fix_text

        text = fix_text(text)
    except Exception:
        pass
    return text, encoding


def detect_delimiter(text: str) -> str:
    """Pick the delimiter that splits rows most consistently.

    csv.Sniffer is unreliable here: German files start with invoice preamble
    lines and contain decimal commas, both of which mislead it.
    """
    lines = [line for line in text.splitlines() if line.strip()][:30]
    if not lines:
        return ";"

    best_delimiter = ";"
    best_score = (0, 0.0)
    for candidate in (";", "\t", "|", ","):
        counts = [line.count(candidate) for line in lines]
        populated = [c for c in counts if c > 0]
        if len(populated) < 2:
            continue
        most_common = max(set(populated), key=populated.count)
        consistency = populated.count(most_common) / len(populated)
        score = (most_common, consistency)
        if score > best_score:
            best_score = score
            best_delimiter = candidate
    return best_delimiter


def fold(value: str) -> str:
    """Case- and diacritic-insensitive key for matching German words."""
    text = str(value).strip().translate(_UMLAUT_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "".join(ch for ch in text.lower() if ch.isalnum())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_german_text.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/german.py tests/faro_docs/test_german_text.py
git commit -m "feat: detect German encodings and delimiters, repair mojibake"
```

---

### Task 4: German-aware header detection

**Files:**
- Create: `src/faro_docs/tables.py`
- Test: `tests/faro_docs/test_header_detection.py`

**Interfaces:**
- Consumes: `fold` from Task 3.
- Produces: `HEADER_VOCABULARY: frozenset[str]` (folded German and English header words); `detect_header_row(rows: list[list], max_scan: int = 15) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_header_detection.py
from src.faro_docs.tables import detect_header_row


def test_finds_header_after_invoice_preamble():
    rows = [
        ["FARO Import-Export GmbH", None, None],
        ["Rechnung Nr. 4711", None, None],
        [None, None, None],
        ["Artikel", "Menge", "Einzelpreis"],
        ["iPhone Display", "3", "149,82"],
        ["USB-C Kabel", "10", "8,50"],
    ]
    assert detect_header_row(rows) == 3


def test_prefers_german_vocabulary_over_mere_text():
    rows = [
        ["Sehr geehrte Damen und Herren", "bitte beachten", "Sie folgendes"],
        ["Pos.", "Bezeichnung", "Betrag"],
        ["1", "Hülle", "12,00"],
    ]
    assert detect_header_row(rows) == 1


def test_first_row_when_already_a_header():
    rows = [["Artikel", "Menge"], ["Hülle", "3"]]
    assert detect_header_row(rows) == 0


def test_handles_empty_input():
    assert detect_header_row([]) == 0


def test_does_not_choose_a_numeric_row():
    rows = [["1", "2", "3"], ["Artikel", "Menge", "Preis"], ["Hülle", "3", "12,00"]]
    assert detect_header_row(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_header_detection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.faro_docs.tables'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/faro_docs/tables.py
"""Table discovery: finding headers, removing totals rows, building Tables."""

import pandas as pd

from src.faro_docs.german import fold

_HEADER_WORDS = [
    # German — what these documents actually use
    "artikel", "artikelnummer", "artikelnr", "bezeichnung", "beschreibung",
    "menge", "anzahl", "stück", "stückzahl", "pos", "position", "einheit",
    "preis", "einzelpreis", "gesamtpreis", "betrag", "summe", "gesamt",
    "netto", "brutto", "mwst", "ust", "steuer", "rabatt", "währung",
    "datum", "lieferdatum", "rechnungsnummer", "kunde", "lieferant", "nr",
    # English equivalents, since mixed exports happen
    "article", "item", "description", "quantity", "qty", "amount", "price",
    "unit", "unitprice", "total", "sum", "date", "customer", "supplier",
]
HEADER_VOCABULARY = frozenset(fold(word) for word in _HEADER_WORDS)


def _looks_numeric(value: object) -> bool:
    text = str(value).strip().replace(".", "").replace(",", "").replace(" ", "")
    return bool(text) and text.lstrip("-").isdigit()


def detect_header_row(rows: list[list], max_scan: int = 15) -> int:
    """Pick the row that best looks like a header.

    Scores non-empty, non-numeric cells, and weights known German header words
    heavily so "Pos. | Bezeichnung | Betrag" beats a long prose line above it.
    """
    best_row, best_score = 0, float("-inf")
    for index in range(min(max_scan, len(rows))):
        row = rows[index]
        filled = [c for c in row if c is not None and str(c).strip()]
        if not filled:
            continue
        numeric = sum(_looks_numeric(c) for c in filled)
        vocabulary = sum(fold(c) in HEADER_VOCABULARY for c in filled)
        score = len(filled) + (vocabulary * 5) - (numeric * 3)
        if score > best_score:
            best_score, best_row = score, index
    return best_row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_header_detection.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/tables.py tests/faro_docs/test_header_detection.py
git commit -m "feat: detect table headers using German header vocabulary"
```

---

### Task 5: Totals-row detection and table assembly

**Files:**
- Modify: `src/faro_docs/tables.py`
- Test: `tests/faro_docs/test_totals_rows.py`

**Interfaces:**
- Consumes: `detect_header_row` (Task 4), `detect_numeric_format`/`to_numeric_series`/`document_numeric_fallback`/`fold` (Tasks 2-3), `ColumnInfo`/`Table` (Task 1).
- Produces: `TOTALS_LABELS: frozenset[str]`; `find_totals_rows(frame: pd.DataFrame, columns: dict[str, ColumnInfo]) -> list[int]`; `build_table(rows: list[list], table_id: str, label: str, fallback_style: str = "german") -> Table`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_totals_rows.py
import pytest

from src.faro_docs.tables import build_table


def test_excludes_labelled_totals_row_from_count_and_sum():
    rows = [
        ["Artikel", "Menge", "Betrag"],
        ["iPhone Display", "3", "149,82"],
        ["USB-C Kabel", "10", "8,50"],
        ["Gesamt", "13", "158,32"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.row_count() == 2
    assert len(table.totals_rows) == 1
    assert table.notes  # the exclusion is stated, not silent


def test_excludes_unlabelled_row_that_equals_the_sum():
    rows = [
        ["Artikel", "Betrag"],
        ["A", "100,00"],
        ["B", "50,00"],
        ["", "150,00"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.row_count() == 2


def test_keeps_a_row_that_only_looks_similar():
    rows = [
        ["Artikel", "Betrag"],
        ["A", "100,00"],
        ["B", "50,00"],
        ["C", "70,00"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.row_count() == 3
    assert table.totals_rows == []


def test_german_amounts_sum_correctly_after_assembly():
    rows = [
        ["Artikel", "Menge"],
        ["A", "1.234"],
        ["B", "2.500"],
        ["C", "750"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.frame["Menge"].sum() == pytest.approx(4484.0)
    assert table.columns["Menge"].numeric_style == "german"


def test_records_the_numeric_decision_for_each_column():
    rows = [["Artikel", "Betrag"], ["A", "12,00"]]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.columns["Betrag"].numeric_rule
    assert table.columns["Artikel"].numeric_style == "none"


def test_drops_empty_rows_and_repeated_headers():
    rows = [
        ["Artikel", "Menge"],
        ["A", "1"],
        [None, None],
        ["Artikel", "Menge"],
        ["B", "2"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.row_count() == 2


def test_names_unnamed_columns_positionally():
    rows = [["Artikel", ""], ["A", "1"]]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert "Spalte 2" in table.frame.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_totals_rows.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_table'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/faro_docs/tables.py`:

```python
from src.faro_docs.german import (
    detect_numeric_format,
    document_numeric_fallback,
    to_numeric_series,
)
from src.faro_docs.model import ColumnInfo, Table

_TOTALS_WORDS = [
    "gesamt", "gesamtsumme", "summe", "zwischensumme", "endbetrag",
    "netto", "brutto", "mwst", "ust", "steuer", "total", "subtotal",
]
TOTALS_LABELS = frozenset(fold(word) for word in _TOTALS_WORDS)

NOTE_TOTALS_EXCLUDED = (
    "Hinweis: {count} Summenzeile(n) wurde(n) von Anzahl und Summen "
    "ausgeschlossen, damit nicht doppelt gezählt wird."
)


def _matches_totals_tolerance(candidate: float, total: float) -> bool:
    """Printed invoices round, so allow 0,5 % or 0,02 absolute, whichever is larger."""
    return abs(candidate - total) <= max(abs(total) * 0.005, 0.02)


def find_totals_rows(frame: pd.DataFrame, columns: dict[str, ColumnInfo]) -> list[int]:
    """Rows that restate a total rather than adding data.

    Counting a "Gesamt" row is a silent doubling and the most likely wrong
    answer on a real invoice, so both labelled and unlabelled totals are found.
    """
    if frame.empty:
        return []

    flagged: set[int] = set()
    for position in range(len(frame)):
        row = frame.iloc[position]
        for value in row:
            if value is not None and fold(str(value)) in TOTALS_LABELS:
                flagged.add(position)
                break

    numeric_columns = [
        name for name, info in columns.items() if info.numeric_style != "none"
    ]
    last = len(frame) - 1
    if last > 0 and last not in flagged:
        for name in numeric_columns:
            series = to_numeric_series(frame[name], columns[name].numeric_style)
            candidate = series.iloc[last]
            preceding = series.iloc[:last].sum()
            if (
                candidate is not None
                and not pd.isna(candidate)
                and preceding
                and _matches_totals_tolerance(float(candidate), float(preceding))
            ):
                flagged.add(last)
                break
    return sorted(flagged)


def build_table(
    rows: list[list], table_id: str, label: str, fallback_style: str = "german"
) -> Table:
    """Turn raw rows into a Table with decided column formats and totals removed."""
    if not rows:
        return Table(id=table_id, label=label, frame=pd.DataFrame())

    header_index = detect_header_row(rows)
    header = rows[header_index]
    body = rows[header_index + 1 :]

    names: list[str] = []
    for position, cell in enumerate(header, start=1):
        text = "" if cell is None else str(cell).strip()
        names.append(text or f"Spalte {position}")

    width = len(names)
    padded = [list(row)[:width] + [None] * max(0, width - len(row)) for row in body]

    header_key = [fold(n) for n in names]
    cleaned_rows = []
    for row in padded:
        if all(cell is None or not str(cell).strip() for cell in row):
            continue
        if [fold(str(c)) if c is not None else "" for c in row] == header_key:
            continue  # a repeated header inside the body
        cleaned_rows.append(row)

    frame = pd.DataFrame(cleaned_rows, columns=names)
    frame = frame.dropna(axis=1, how="all").reset_index(drop=True)

    if fallback_style == "german":
        fallback_style = document_numeric_fallback(
            [frame[name].tolist() for name in frame.columns]
        )

    columns: dict[str, ColumnInfo] = {}
    for name in frame.columns:
        style, rule, confident = detect_numeric_format(
            frame[name].tolist(), fallback_style=fallback_style
        )
        columns[name] = ColumnInfo(
            name=name, numeric_style=style, numeric_rule=rule, numeric_confident=confident
        )

    totals_positions = find_totals_rows(frame, columns)
    totals_rows = [frame.iloc[p].to_dict() for p in totals_positions]
    if totals_positions:
        frame = frame.drop(index=frame.index[totals_positions]).reset_index(drop=True)

    for name, info in columns.items():
        if info.numeric_style in {"german", "english", "integer"}:
            frame[name] = to_numeric_series(frame[name], info.numeric_style)

    notes: list[str] = []
    if totals_rows:
        notes.append(NOTE_TOTALS_EXCLUDED.format(count=len(totals_rows)))
    for info in columns.values():
        if not info.numeric_confident and info.numeric_style != "none":
            notes.append(f"Spalte „{info.name}“: {info.numeric_rule}.")

    return Table(
        id=table_id,
        label=label,
        frame=frame,
        columns=columns,
        totals_rows=totals_rows,
        notes=notes,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/ -v`
Expected: PASS (all tests, including earlier tasks)

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/tables.py tests/faro_docs/test_totals_rows.py
git commit -m "feat: exclude totals rows and record column formats when building tables"
```

---

### Task 6: Tabular ingest — CSV and every Excel sheet

**Files:**
- Create: `src/faro_docs/ingest/__init__.py`
- Create: `src/faro_docs/ingest/tabular.py`
- Test: `tests/faro_docs/test_ingest_tabular.py`

**Interfaces:**
- Consumes: `decode_text`/`detect_delimiter` (Task 3), `build_table` (Task 5), `Document` (Task 1).
- Produces: `ingest_csv(raw: bytes, document_id: str, filename: str) -> Document`; `ingest_excel(raw: bytes, document_id: str, filename: str, legacy: bool = False) -> Document`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_ingest_tabular.py
import io

import pandas as pd
import pytest

from src.faro_docs.ingest.tabular import ingest_csv, ingest_excel


def test_reads_german_csv_end_to_end():
    raw = "Artikel;Menge;Betrag\nHülle;1.234;12,00\nKabel;2.500;8,50\n".encode("cp1252")
    doc = ingest_csv(raw, document_id="dok1", filename="liste.csv")
    table = doc.tables[0]
    assert table.row_count() == 2
    assert table.frame["Menge"].sum() == pytest.approx(3734.0)
    assert table.frame["Betrag"].sum() == pytest.approx(20.5)
    assert "Hülle" in doc.text


def test_skips_csv_preamble():
    raw = "Rechnung Nr. 4711\nKunde: Müller GmbH\n\nArtikel;Menge\nHülle;3\n".encode("utf-8")
    doc = ingest_csv(raw, document_id="dok1", filename="r.csv")
    assert list(doc.tables[0].frame.columns) == ["Artikel", "Menge"]
    assert doc.tables[0].row_count() == 1


def test_reads_every_excel_sheet(tmp_path):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"Artikel": ["A", "B"], "Menge": [1, 2]}).to_excel(
            writer, sheet_name="Januar", index=False
        )
        pd.DataFrame({"Artikel": ["C"], "Menge": [3]}).to_excel(
            writer, sheet_name="Februar", index=False
        )
    doc = ingest_excel(buffer.getvalue(), document_id="dok1", filename="umsatz.xlsx")
    assert len(doc.tables) == 2
    labels = {table.label for table in doc.tables}
    assert "Januar" in " ".join(labels) and "Februar" in " ".join(labels)
    assert doc.total_rows() == 3


def test_skips_empty_excel_sheets():
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"Artikel": ["A"]}).to_excel(writer, sheet_name="Daten", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="Leer", index=False)
    doc = ingest_excel(buffer.getvalue(), document_id="dok1", filename="x.xlsx")
    assert len(doc.tables) == 1


def test_ragged_csv_rows_do_not_crash():
    raw = "Artikel;Menge\nA;1;überzählig\nB\n".encode("utf-8")
    doc = ingest_csv(raw, document_id="dok1", filename="ragged.csv")
    assert doc.tables[0].row_count() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_ingest_tabular.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.faro_docs.ingest'`

- [ ] **Step 3: Write minimal implementation**

Create `src/faro_docs/ingest/__init__.py` as an empty file, then:

```python
# src/faro_docs/ingest/tabular.py
"""CSV and Excel ingest. Every sheet becomes its own table."""

import csv
import io

import pandas as pd

from src.faro_docs.german import decode_text, detect_delimiter
from src.faro_docs.model import Document
from src.faro_docs.tables import build_table

NOTE_ENCODING = "Datei wurde als {encoding} gelesen."


def _rows_to_text(rows: list[list]) -> str:
    return "\n".join(
        "; ".join("" if cell is None else str(cell) for cell in row) for row in rows
    )


def ingest_csv(raw: bytes, document_id: str, filename: str) -> Document:
    text, encoding = decode_text(raw)
    delimiter = detect_delimiter(text)
    rows = [
        row for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    ]
    width = max((len(row) for row in rows), default=0)
    padded = [row + [None] * (width - len(row)) for row in rows]

    table = build_table(padded, table_id=f"{document_id}:tabelle1", label="Tabelle 1")
    return Document(
        id=document_id,
        filename=filename,
        media_type="text/csv",
        text=text,
        tables=[table] if table.row_count() else [],
        notes=[NOTE_ENCODING.format(encoding=encoding)],
    )


def ingest_excel(
    raw: bytes, document_id: str, filename: str, legacy: bool = False
) -> Document:
    engine = "xlrd" if legacy else "openpyxl"
    sheets = pd.read_excel(io.BytesIO(raw), header=None, sheet_name=None, engine=engine)

    tables = []
    text_parts = []
    for index, (sheet_name, frame) in enumerate(sheets.items(), start=1):
        frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")
        if frame.empty:
            continue
        rows = frame.where(pd.notna(frame), None).values.tolist()
        table = build_table(
            rows,
            table_id=f"{document_id}:blatt{index}",
            label=f"Blatt „{sheet_name}“",
        )
        if table.row_count():
            tables.append(table)
            text_parts.append(f"# Blatt „{sheet_name}“\n{_rows_to_text(rows)}")

    return Document(
        id=document_id,
        filename=filename,
        media_type="application/vnd.ms-excel" if legacy else
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        text="\n\n".join(text_parts),
        tables=tables,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_ingest_tabular.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/ingest/ tests/faro_docs/test_ingest_tabular.py
git commit -m "feat: ingest German CSV and every Excel sheet, not just the first"
```

---

### Task 7: PDF ingest with multi-table support

Port the working Phase 0 logic onto the new numeric layer. Multi-page tables merge by matching header; genuinely different tables stay separate.

**Files:**
- Create: `src/faro_docs/ingest/pdf_ingest.py`
- Test: `tests/faro_docs/test_ingest_pdf.py`

**Interfaces:**
- Consumes: `build_table` (Task 5), `Document` (Task 1).
- Produces: `ingest_pdf(raw: bytes, document_id: str, filename: str) -> Document`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_ingest_pdf.py
import pytest

from src.faro_docs.ingest.pdf_ingest import ingest_pdf

reportlab = pytest.importorskip("reportlab")


def _build_pdf(path, sections):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    elements = []
    for index, (title, header, body) in enumerate(sections):
        if index:
            elements.append(PageBreak())
        elements.append(Paragraph(title, styles["Title"]))
        elements.append(Table([header] + body, repeatRows=1))
    SimpleDocTemplate(str(path), pagesize=A4).build(elements)
    return path


def test_keeps_two_different_tables_separate(tmp_path):
    path = _build_pdf(
        tmp_path / "zwei.pdf",
        [
            ("FARO GmbH", ["Artikel", "Menge"], [["A", "1"], ["B", "2"]]),
            ("Lagerhaus Müller", ["Position", "Anzahl"], [["C", "3"]]),
        ],
    )
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="zwei.pdf")
    assert len(doc.tables) == 2
    assert doc.total_rows() == 3


def test_merges_one_table_split_across_pages(tmp_path):
    body = [[f"Artikel {i}", str(i)] for i in range(1, 80)]
    path = _build_pdf(tmp_path / "lang.pdf", [("FARO GmbH", ["Artikel", "Menge"], body)])
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="lang.pdf")
    assert len(doc.tables) == 1
    assert doc.tables[0].row_count() == 79


def test_text_is_always_available(tmp_path):
    path = _build_pdf(tmp_path / "t.pdf", [("FARO GmbH", ["Artikel"], [["A"]])])
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="t.pdf")
    assert "FARO" in doc.text


def test_pdf_without_tables_still_returns_text(tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet

    path = tmp_path / "brief.pdf"
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [Paragraph("Sehr geehrte Damen und Herren", getSampleStyleSheet()["Normal"])]
    )
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="brief.pdf")
    assert doc.tables == []
    assert "Sehr geehrte" in doc.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_ingest_pdf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.faro_docs.ingest.pdf_ingest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/faro_docs/ingest/pdf_ingest.py
"""PDF ingest. Keeps every distinct table and merges ones split across pages."""

from src.faro_docs.model import Document
from src.faro_docs.tables import build_table

NOTE_SCAN_SUSPECTED = (
    "Diese Datei enthält kaum auslesbaren Text und ist vermutlich ein Scan. "
    "Texterkennung ist in dieser Version noch nicht aktiv."
)
_MIN_TEXT_PER_PAGE = 20


def ingest_pdf(raw: bytes, document_id: str, filename: str) -> Document:
    import fitz  # PyMuPDF

    document = fitz.open(stream=raw, filetype="pdf")
    try:
        page_texts: list[str] = []
        groups: dict[tuple, list[list]] = {}

        for page in document:
            page_texts.append(page.get_text())
            for found in page.find_tables().tables:
                rows = found.extract()
                if len(rows) < 2:
                    continue
                header = tuple(
                    "" if cell is None else str(cell).strip() for cell in rows[0]
                )
                groups.setdefault(header, []).extend(rows[1:])

        text = "\n\n".join(page_texts)
        notes: list[str] = []
        if len(text.strip()) < _MIN_TEXT_PER_PAGE * max(1, len(document)):
            notes.append(NOTE_SCAN_SUSPECTED)

        tables = []
        ordered = sorted(groups.items(), key=lambda item: -len(item[1]))
        for index, (header, body) in enumerate(ordered, start=1):
            table = build_table(
                [list(header)] + body,
                table_id=f"{document_id}:tabelle{index}",
                label=f"Tabelle {index}",
            )
            if table.row_count():
                tables.append(table)

        return Document(
            id=document_id,
            filename=filename,
            media_type="application/pdf",
            text=text,
            tables=tables,
            notes=notes,
        )
    finally:
        document.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_ingest_pdf.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/ingest/pdf_ingest.py tests/faro_docs/test_ingest_pdf.py
git commit -m "feat: ingest PDFs keeping every distinct table on the German numeric layer"
```

---

### Task 8: Format router and multi-file handling

**Files:**
- Create: `src/faro_docs/ingest/router.py`
- Test: `tests/faro_docs/test_router.py` (replaces the old `tests/test_router.py` in Task 16)

**Interfaces:**
- Consumes: all ingest functions (Tasks 6-7).
- Produces: `detect_kind(filename: str, raw: bytes) -> str` returning one of `"pdf" | "excel" | "excel_legacy" | "csv" | "text" | "unknown"`; `ingest_one(raw: bytes, filename: str, document_id: str) -> Document`; `ingest_all(files: list[tuple[str, bytes]]) -> list[Document]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_router.py
import io

import pandas as pd

from src.faro_docs.ingest.router import detect_kind, ingest_all, ingest_one


def _xlsx_bytes():
    buffer = io.BytesIO()
    pd.DataFrame({"Artikel": ["A"], "Menge": [1]}).to_excel(buffer, index=False)
    return buffer.getvalue()


class TestDetection:
    def test_detects_by_magic_bytes_not_just_extension(self):
        assert detect_kind("falsch_benannt.csv", _xlsx_bytes()) == "excel"
        assert detect_kind("falsch_benannt.xlsx", b"%PDF-1.7\n...") == "pdf"

    def test_detects_csv(self):
        assert detect_kind("liste.csv", b"Artikel;Menge\nA;1\n") == "csv"

    def test_unknown_type(self):
        assert detect_kind("zeichnung.dwg", b"\x00\x01\x02binary") == "unknown"


class TestIngestAll:
    def test_processes_every_file_not_only_the_first(self):
        files = [
            ("a.csv", "Artikel;Menge\nA;1\n".encode()),
            ("b.csv", "Artikel;Menge\nB;2\nC;3\n".encode()),
        ]
        documents = ingest_all(files)
        assert len(documents) == 2
        assert sum(doc.total_rows() for doc in documents) == 3

    def test_document_ids_are_unique_and_tables_namespaced(self):
        files = [("a.csv", b"Artikel;Menge\nA;1\n"), ("b.csv", b"Artikel;Menge\nB;2\n")]
        documents = ingest_all(files)
        ids = {doc.id for doc in documents}
        assert len(ids) == 2
        table_ids = {t.id for doc in documents for t in doc.tables}
        assert len(table_ids) == 2

    def test_one_bad_file_does_not_stop_the_others(self):
        files = [
            ("kaputt.pdf", b"nicht wirklich ein PDF"),
            ("gut.csv", "Artikel;Menge\nA;1\n".encode()),
        ]
        documents = ingest_all(files)
        assert len(documents) == 2
        broken = [d for d in documents if d.filename == "kaputt.pdf"][0]
        assert broken.notes  # German explanation, no crash

    def test_unknown_type_is_reported_in_german(self):
        documents = ingest_all([("zeichnung.dwg", b"\x00\x01binary")])
        assert documents[0].notes
        assert documents[0].tables == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.faro_docs.ingest.router'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/faro_docs/ingest/router.py
"""Dispatch uploads by sniffed content, and process every file."""

from src.faro_docs.ingest.pdf_ingest import ingest_pdf
from src.faro_docs.ingest.tabular import ingest_csv, ingest_excel
from src.faro_docs.model import Document

NOTE_UNSUPPORTED = (
    "Dateityp „{suffix}“ wird nicht unterstützt. Unterstützt werden derzeit "
    "PDF, Excel (XLSX/XLS), CSV und Textdateien."
)
NOTE_UNREADABLE = "Die Datei „{filename}“ konnte nicht gelesen werden: {error}"


def detect_kind(filename: str, raw: bytes) -> str:
    """Sniff content first; users rename files and extensions lie."""
    if raw.startswith(b"%PDF"):
        return "pdf"
    if raw.startswith(b"PK\x03\x04") and b"xl/" in raw[:4096]:
        return "excel"
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        return "excel_legacy"

    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix in {"xlsx", "xlsm"}:
        return "excel"
    if suffix == "xls":
        return "excel_legacy"
    if suffix in {"csv", "tsv"}:
        return "csv"
    if suffix in {"txt", "md"}:
        return "text"

    sample = raw[:2048]
    if sample and b"\x00" not in sample:
        try:
            sample.decode("utf-8")
            return "csv" if any(d in sample for d in b";,\t") else "text"
        except UnicodeDecodeError:
            pass
    return "unknown"


def ingest_one(raw: bytes, filename: str, document_id: str) -> Document:
    kind = detect_kind(filename, raw)
    try:
        if kind == "pdf":
            return ingest_pdf(raw, document_id, filename)
        if kind == "excel":
            return ingest_excel(raw, document_id, filename)
        if kind == "excel_legacy":
            return ingest_excel(raw, document_id, filename, legacy=True)
        if kind == "csv":
            return ingest_csv(raw, document_id, filename)
        if kind == "text":
            from src.faro_docs.german import decode_text

            text, _ = decode_text(raw)
            return Document(
                id=document_id, filename=filename, media_type="text/plain", text=text
            )
    except Exception as error:  # degrade, never abort the whole upload
        return Document(
            id=document_id,
            filename=filename,
            media_type="",
            notes=[NOTE_UNREADABLE.format(filename=filename, error=error)],
        )

    suffix = filename.rsplit(".", 1)[-1] if "." in filename else "?"
    return Document(
        id=document_id,
        filename=filename,
        media_type="",
        notes=[NOTE_UNSUPPORTED.format(suffix=suffix)],
    )


def ingest_all(files: list[tuple[str, bytes]]) -> list[Document]:
    """Every uploaded file becomes its own Document with namespaced table ids."""
    return [
        ingest_one(raw, filename, document_id=f"dok{index}")
        for index, (filename, raw) in enumerate(files, start=1)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/ingest/router.py tests/faro_docs/test_router.py
git commit -m "feat: route uploads by sniffed type and process every file"
```

---

### Task 9: Facts with provenance

**Files:**
- Create: `src/faro_docs/facts.py`
- Test: `tests/faro_docs/test_facts.py`

**Interfaces:**
- Consumes: `Document`/`Table`/`ColumnInfo` (Task 1).
- Produces: `compute_facts(documents: list[Document]) -> dict` — a JSON-safe dict with a `_zusammenfassung` block carrying code-computed cross-document totals.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_facts.py
import pandas as pd
import pytest

from src.faro_docs.facts import compute_facts
from src.faro_docs.model import ColumnInfo, Document, Table


def _document(doc_id, frame, table_id="t1"):
    columns = {
        name: ColumnInfo(name=name, numeric_style="german" if frame[name].dtype != object else "none")
        for name in frame.columns
    }
    return Document(
        id=doc_id,
        filename=f"{doc_id}.csv",
        media_type="text/csv",
        tables=[Table(id=f"{doc_id}:{table_id}", label="Tabelle 1", frame=frame, columns=columns)],
    )


def test_reports_row_counts_per_table():
    docs = [_document("dok1", pd.DataFrame({"Menge": [1.0, 2.0, 3.0]}))]
    facts = compute_facts(docs)
    assert facts["dok1"]["tabellen"]["dok1:t1"]["zeilen"] == 3


def test_computes_column_statistics():
    docs = [_document("dok1", pd.DataFrame({"Menge": [1.0, 2.0, 3.0]}))]
    stats = compute_facts(docs)["dok1"]["tabellen"]["dok1:t1"]["spalten"]["Menge"]
    assert stats["summe"] == pytest.approx(6.0)
    assert stats["min"] == pytest.approx(1.0)
    assert stats["max"] == pytest.approx(3.0)


def test_cross_document_total_is_computed_in_code():
    docs = [
        _document("dok1", pd.DataFrame({"Menge": [1.0] * 250})),
        _document("dok2", pd.DataFrame({"Menge": [1.0] * 200})),
    ]
    summary = compute_facts(docs)["_zusammenfassung"]
    assert summary["zeilen_gesamt"] == 450
    assert summary["dokumente"] == 2
    assert summary["tabellen"] == 2


def test_facts_are_json_safe():
    import json

    docs = [_document("dok1", pd.DataFrame({"Menge": [1.0], "Artikel": ["A"]}))]
    json.dumps(compute_facts(docs), ensure_ascii=False)


def test_empty_input_does_not_crash():
    assert compute_facts([])["_zusammenfassung"]["zeilen_gesamt"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_facts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.faro_docs.facts'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/faro_docs/facts.py
"""Deterministic facts, computed in code so the model never has to do arithmetic."""

import pandas as pd

from src.faro_docs.model import Document


def _column_stats(series: pd.Series) -> dict:
    numeric = pd.to_numeric(series, errors="coerce")
    has_numbers = bool(numeric.notna().any())
    return {
        "summe": float(numeric.sum()) if has_numbers else None,
        "min": float(numeric.min()) if has_numbers else None,
        "max": float(numeric.max()) if has_numbers else None,
        "durchschnitt": float(numeric.mean()) if has_numbers else None,
        "verschiedene_werte": int(series.nunique(dropna=True)),
    }


def compute_facts(documents: list[Document]) -> dict:
    """Facts for every document and a code-computed cross-document summary.

    The summary exists because the model gets cross-table arithmetic wrong even
    with the right numbers in front of it (it answered 430 for 250 + 200).
    """
    facts: dict = {}
    total_rows = 0
    total_tables = 0

    for document in documents:
        tables: dict = {}
        for table in document.tables:
            tables[table.id] = {
                "bezeichnung": table.label,
                "zeilen": table.row_count(),
                "spalten": {
                    str(name): _column_stats(table.frame[name])
                    for name in table.frame.columns
                },
                "spaltenformate": {
                    str(name): info.numeric_rule
                    for name, info in table.columns.items()
                    if info.numeric_style != "none"
                },
                "hinweise": list(table.notes),
            }
            total_rows += table.row_count()
            total_tables += 1

        facts[document.id] = {
            "dateiname": document.filename,
            "tabellen": tables,
            "hinweise": list(document.notes),
        }

    facts["_zusammenfassung"] = {
        "dokumente": len(documents),
        "tabellen": total_tables,
        "zeilen_gesamt": total_rows,
    }
    return facts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_facts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/facts.py tests/faro_docs/test_facts.py
git commit -m "feat: compute facts with code-computed cross-document totals"
```

---

### Task 10: German messages

**Files:**
- Create: `src/faro_docs/messages_de.py`
- Test: `tests/faro_docs/test_messages.py`

**Interfaces:**
- Consumes: nothing.
- Produces: module-level German string constants, and `provenance_footer(sources: set[str], notes: list[str]) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_messages.py
import re

from src.faro_docs import messages_de


def test_every_public_string_is_german_not_english():
    english_giveaways = re.compile(
        r"\b(the|please|error|file|table|row|column|could not|failed)\b", re.IGNORECASE
    )
    for name in dir(messages_de):
        if name.startswith("_"):
            continue
        value = getattr(messages_de, name)
        if isinstance(value, str):
            assert not english_giveaways.search(value), f"{name} contains English"


def test_footer_names_the_source_of_the_numbers():
    footer = messages_de.provenance_footer({"tabelle"}, [])
    assert "berechnet" in footer.lower()


def test_footer_includes_notes():
    footer = messages_de.provenance_footer({"text"}, ["Summenzeile ausgeschlossen"])
    assert "Summenzeile ausgeschlossen" in footer


def test_footer_is_empty_without_input():
    assert messages_de.provenance_footer(set(), []) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_messages.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/faro_docs/messages_de.py
"""Every string a user can see. German only -- the users are non-technical Germans."""

KEINE_DATEI = "Bitte hängen Sie eine Datei an Ihre Frage an (PDF, Excel, CSV oder Text)."
EXTRAHIERE = "_(Dokument wird ausgewertet …)_"
DENKT_NACH = "_(arbeitet noch, {sekunden} s …)_"
KEINE_TABELLE = (
    "In dieser Datei wurde keine auswertbare Tabelle gefunden. "
    "Fragen nach genauen Anzahlen oder Summen kann ich deshalb nicht sicher beantworten."
)
OLLAMA_NICHT_ERREICHBAR = (
    "Das Sprachmodell ist unter `{host}` nicht erreichbar. "
    "Bitte prüfen Sie im Admin-Bereich unter Funktionen die Einstellung OLLAMA_HOST."
)
ZU_VIELE_RUNDEN = (
    "Ich konnte innerhalb der erlaubten Schritte keine endgültige Antwort bilden. "
    "Bitte formulieren Sie die Frage etwas genauer."
)
RAG_WARNUNG = (
    "**Achtung: Die eingebaute Dateiverarbeitung von Open WebUI ist aktiv.**\n"
    "Sie ersetzt Ihre Frage durch einen eigenen Text, bevor diese Funktion sie "
    "sieht — die Antworten sind dadurch unzuverlässig.\n"
    "Zu beheben im Admin-Bereich: für dieses Modell die Fähigkeit "
    "`file_context` abschalten.\n"
)

_QUELLEN = {
    "tabelle": "aus der Tabelle berechnet",
    "text": "aus dem Dokumenttext gelesen",
    "ocr": "per Texterkennung gelesen (unsicher)",
}


def provenance_footer(sources: set[str], notes: list[str]) -> str:
    """One short German line saying where the answer came from."""
    parts = [_QUELLEN[s] for s in ("tabelle", "text", "ocr") if s in sources]
    if not parts and not notes:
        return ""
    lines = []
    if parts:
        lines.append("_Herkunft: " + ", ".join(parts) + "._")
    lines.extend(f"_{note}_" for note in notes)
    return "\n\n" + "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_messages.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/messages_de.py tests/faro_docs/test_messages.py
git commit -m "feat: add German user-facing messages and provenance footer"
```

---

### Task 11: Tools and the answering loop

**Files:**
- Create: `src/faro_docs/answer.py`
- Test: `tests/faro_docs/test_answer.py`

**Interfaces:**
- Consumes: `Document` (Task 1), `compute_facts` (Task 9), `messages_de` (Task 10).
- Produces: `TOOL_SCHEMAS: list[dict]`; `SYSTEM_PROMPT: str`; `run_tool(name: str, args: dict, documents: list[Document]) -> object`; `build_context(documents: list[Document], max_text_chars: int = 40000) -> str`; `answer(documents, question, model, host, max_rounds=6) -> Iterator[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_answer.py
import pandas as pd
import pytest

from src.faro_docs.answer import TOOL_SCHEMAS, build_context, run_tool
from src.faro_docs.model import ColumnInfo, Document, Table


def _documents():
    frame_a = pd.DataFrame({"Artikel": ["A", "B"], "Menge": [1.0, 2.0]})
    frame_b = pd.DataFrame({"Artikel": ["C"], "Menge": [3.0]})
    return [
        Document(
            id="dok1", filename="a.csv", media_type="text/csv", text="FARO GmbH",
            tables=[Table(id="dok1:t1", label="Tabelle 1", frame=frame_a,
                          columns={"Menge": ColumnInfo("Menge", "german", "", True)})],
        ),
        Document(
            id="dok2", filename="b.csv", media_type="text/csv", text="Lagerhaus Müller",
            tables=[Table(id="dok2:t1", label="Tabelle 1", frame=frame_b)],
        ),
    ]


class TestTools:
    def test_list_documents_names_every_file(self):
        result = run_tool("list_documents", {}, _documents())
        assert set(result) == {"dok1", "dok2"}

    def test_list_tables_reports_ids_and_row_counts(self):
        result = run_tool("list_tables", {}, _documents())
        assert result["dok1:t1"]["zeilen"] == 2

    def test_count_rows_for_one_table(self):
        assert run_tool("count_rows", {"table": "dok1:t1"}, _documents()) == 2

    def test_count_rows_all_is_computed_in_code(self):
        assert run_tool("count_rows", {"table": "alle"}, _documents()) == 3

    def test_sum_column(self):
        assert run_tool("sum_column", {"table": "dok1:t1", "column": "Menge"},
                        _documents()) == pytest.approx(3.0)

    def test_sum_of_non_numeric_column_raises_rather_than_returning_zero(self):
        with pytest.raises(ValueError):
            run_tool("sum_column", {"table": "dok1:t1", "column": "Artikel"}, _documents())

    def test_unknown_table_names_the_valid_ids(self):
        with pytest.raises(ValueError) as excinfo:
            run_tool("count_rows", {"table": "gibtsnicht"}, _documents())
        assert "dok1:t1" in str(excinfo.value)

    def test_get_row(self):
        row = run_tool("get_row", {"table": "dok1:t1", "index": 0}, _documents())
        assert row["Artikel"] == "A"

    def test_find_rows_filters(self):
        rows = run_tool("find_rows", {"table": "dok1:t1", "column": "Artikel",
                                      "contains": "B"}, _documents())
        assert len(rows) == 1 and rows[0]["Artikel"] == "B"


class TestSchemas:
    def test_every_schema_requires_an_explicit_table_except_the_listers(self):
        for schema in TOOL_SCHEMAS:
            function = schema["function"]
            if function["name"] in {"list_documents", "list_tables"}:
                continue
            assert "table" in function["parameters"]["required"]


class TestContext:
    def test_context_contains_text_tables_and_facts(self):
        context = build_context(_documents())
        assert "FARO GmbH" in context
        assert "Lagerhaus Müller" in context
        assert "dok1:t1" in context
        assert "zeilen_gesamt" in context

    def test_long_text_is_truncated_visibly_not_silently(self):
        documents = _documents()
        documents[0].text = "x" * 100_000
        context = build_context(documents, max_text_chars=1000)
        assert len(context) < 60_000
        assert "gekürzt" in context.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_answer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.faro_docs.answer'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/faro_docs/answer.py
"""Tools the model may call, and the loop that runs them."""

import json
import queue
import threading
import time
from collections.abc import Iterator

import pandas as pd

from src.faro_docs import messages_de
from src.faro_docs.facts import compute_facts
from src.faro_docs.model import Document

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_documents",
        "description": "Alle hochgeladenen Dokumente mit Dateiname und Tabellenzahl auflisten.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "list_tables",
        "description": (
            "Alle erkannten Tabellen mit Id, Spalten und Zeilenzahl auflisten. "
            "IMMER zuerst aufrufen, wenn nach der Anzahl der Tabellen, Blätter "
            "oder Dokumente gefragt wird."
        ),
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "count_rows",
        "description": (
            "Zeilen einer Tabelle zählen. Mit table='alle' die Gesamtzahl über "
            "alle Tabellen und Dokumente hinweg."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Tabellen-Id oder 'alle'"},
        }, "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "sum_column",
        "description": "Eine Zahlenspalte einer Tabelle summieren.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
        }, "required": ["table", "column"]},
    }},
    {"type": "function", "function": {
        "name": "get_row",
        "description": "Eine einzelne Zeile über ihren nullbasierten Index holen.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "index": {"type": "integer"},
        }, "required": ["table", "index"]},
    }},
    {"type": "function", "function": {
        "name": "find_rows",
        "description": "Zeilen suchen, deren Spalte einen Text enthält.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
            "contains": {"type": "string"},
        }, "required": ["table", "column", "contains"]},
    }},
]

SYSTEM_PROMPT = (
    "Du beantwortest Fragen zu hochgeladenen Dokumenten. Es kann sich um "
    "alles handeln: Rechnungen, Lieferscheine, Verträge, Berichte, Listen.\n\n"
    "Regeln:\n"
    "1. Zahlen, Anzahlen und Summen NIE selbst zählen oder addieren. Rufe das "
    "passende Werkzeug auf. Deine eigene Rechnung ist nicht verlässlich.\n"
    "2. Fragen nach der Anzahl der Tabellen, Blätter oder Dokumente IMMER mit "
    "list_tables beziehungsweise list_documents beantworten, nie schätzen.\n"
    "3. Eine Datei kann mehrere Tabellen enthalten, und es können mehrere "
    "Dateien hochgeladen sein. Prüfe das, bevor du eine Zahl nennst.\n"
    "4. Für Gesamtzahlen über alles hinweg count_rows mit table='alle' nutzen "
    "oder den Wert aus FAKTEN._zusammenfassung übernehmen.\n"
    "5. Inhaltliche Fragen (Worum geht es? Wer ist der Absender? Was steht in "
    "Abschnitt 4?) direkt aus dem Dokumenttext beantworten.\n"
    "6. Steht die Antwort nicht im Dokument, sage genau das: "
    "„Das steht nicht im Dokument.“ Nichts erfinden.\n"
    "7. Antworte auf Deutsch, in ganzen Sätzen, knapp."
)


def _all_tables(documents: list[Document]) -> dict:
    return {table.id: table for document in documents for table in document.tables}


def run_tool(name: str, args: dict, documents: list[Document]):
    if name == "list_documents":
        return {
            document.id: {
                "dateiname": document.filename,
                "tabellen": [table.id for table in document.tables],
            }
            for document in documents
        }

    tables = _all_tables(documents)

    if name == "list_tables":
        return {
            table_id: {
                "bezeichnung": table.label,
                "spalten": [str(c) for c in table.frame.columns],
                "zeilen": table.row_count(),
            }
            for table_id, table in tables.items()
        }

    if name == "count_rows" and args.get("table") == "alle":
        return sum(table.row_count() for table in tables.values())

    table_id = args.get("table")
    if table_id not in tables:
        raise ValueError(
            f"Unbekannte Tabelle „{table_id}“. Gültige Tabellen: {list(tables)}"
        )
    table = tables[table_id]

    if name == "count_rows":
        return table.row_count()

    if name == "sum_column":
        column = args["column"]
        if column not in table.frame.columns:
            raise ValueError(
                f"Spalte „{column}“ gibt es nicht. Vorhanden: {list(table.frame.columns)}"
            )
        numeric = pd.to_numeric(table.frame[column], errors="coerce")
        if len(table.frame) and numeric.notna().sum() == 0:
            raise ValueError(f"Spalte „{column}“ enthält keine Zahlen zum Summieren.")
        return float(numeric.sum())

    if name == "get_row":
        return table.frame.iloc[int(args["index"])].to_dict()

    if name == "find_rows":
        column = args["column"]
        if column not in table.frame.columns:
            raise ValueError(
                f"Spalte „{column}“ gibt es nicht. Vorhanden: {list(table.frame.columns)}"
            )
        needle = str(args["contains"]).lower()
        mask = table.frame[column].astype(str).str.lower().str.contains(needle, na=False)
        return table.frame[mask].head(50).to_dict(orient="records")

    raise ValueError(f"Unbekanntes Werkzeug: {name}")


def build_context(documents: list[Document], max_text_chars: int = 40000) -> str:
    """Document text, table markdown, and code-computed facts."""
    parts = []
    for document in documents:
        text = document.text or ""
        if len(text) > max_text_chars:
            text = text[:max_text_chars] + "\n…[Text gekürzt]"
        parts.append(f"## Dokument {document.id}: {document.filename}\n{text}")
        for table in document.tables:
            parts.append(
                f"### {table.id} — {table.label} "
                f"(Spalten: {', '.join(str(c) for c in table.frame.columns)})\n"
                + table.frame.head(200).to_markdown(index=False)
            )
    parts.append(
        "FAKTEN: " + json.dumps(compute_facts(documents), ensure_ascii=False, default=str)
    )
    return "\n\n".join(parts)


_HEARTBEAT = object()
_DONE = object()


def _stream_with_heartbeat(client, model, messages, tools, interval=3.0):
    """Ollama prefills a long document before emitting anything; a silent gap
    that long drops the browser connection, so emit a heartbeat while waiting."""
    channel: queue.Queue = queue.Queue()

    def worker():
        try:
            for chunk in client.chat(
                model=model, messages=messages, tools=tools, stream=True
            ):
                channel.put(chunk)
        except Exception as error:
            channel.put(error)
        finally:
            channel.put(_DONE)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        try:
            item = channel.get(timeout=interval)
        except queue.Empty:
            yield _HEARTBEAT
            continue
        if item is _DONE:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def answer(
    documents: list[Document],
    question: str,
    model: str,
    host: str,
    max_rounds: int = 6,
) -> Iterator[str]:
    """Plain sync generator -- async pipes never signal completion (open-webui#20196)."""
    import ollama

    client = ollama.Client(host=host)
    context = build_context(documents)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"DOKUMENTE:\n{context}\n\nFRAGE: {question}"},
    ]
    tools = TOOL_SCHEMAS if _all_tables(documents) else None
    used_tools = False

    for _ in range(max_rounds):
        content = ""
        tool_calls = None
        started = time.monotonic()
        seen_output = False
        try:
            for item in _stream_with_heartbeat(client, model, messages, tools):
                if item is _HEARTBEAT:
                    if not seen_output:
                        yield messages_de.DENKT_NACH.format(
                            sekunden=int(time.monotonic() - started)
                        ) + " "
                    continue
                seen_output = True
                piece = item.get("message", {}).get("content", "")
                if piece:
                    content += piece
                    yield piece
                if item.get("message", {}).get("tool_calls"):
                    tool_calls = item["message"]["tool_calls"]
        except Exception as error:
            yield "\n\n" + messages_de.OLLAMA_NICHT_ERREICHBAR.format(host=host)
            yield f"\n\n_({error})_"
            return

        messages.append(
            {"role": "assistant", "content": content, "tool_calls": tool_calls}
        )
        if not tool_calls:
            sources = {"tabelle"} if used_tools else {"text"}
            notes = [n for d in documents for n in d.notes]
            notes += [n for d in documents for t in d.tables for n in t.notes]
            yield messages_de.provenance_footer(sources, notes)
            return

        used_tools = True
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            try:
                result = run_tool(name, args, documents)
            except Exception as error:
                result = {"fehler": str(error)}
            yield f"\n_- `{name}({args})` → `{result}`_\n"
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                    "tool_name": name,
                }
            )

    yield "\n\n" + messages_de.ZU_VIELE_RUNDEN
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_answer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/faro_docs/answer.py tests/faro_docs/test_answer.py
git commit -m "feat: add tools, German system prompt, and answering loop with honesty footer"
```

---

### Task 12: Open WebUI adapter

**Files:**
- Create: `adapters/__init__.py`
- Create: `adapters/openwebui_pipe.py`
- Test: `tests/faro_docs/test_adapter.py`

**Interfaces:**
- Consumes: `ingest_all` (Task 8), `answer` (Task 11), `messages_de` (Task 10).
- Produces: `looks_like_openwebui_rag(message: str) -> bool`; `recover_question(message: str) -> str`; `collect_files(files: list) -> list[tuple[str, bytes]]`; `class Pipe`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_adapter.py
import os

from adapters.openwebui_pipe import (
    Pipe,
    collect_files,
    looks_like_openwebui_rag,
    recover_question,
)

RAG_MESSAGE = (
    "### Task:\nRespond to the user query using the provided context, "
    'incorporating inline citations in the format [id] only when the <source> '
    'tag includes an explicit id attribute (e.g., <source id="1">).\n\n'
    "### Guidelines:\n- If you don't know the answer, clearly state that.\n\n"
    "<context>...</context>\n\n<user_query>\nWie viele Zeilen?\n</user_query>"
)


class TestRagDetection:
    def test_detects_open_webui_template(self):
        assert looks_like_openwebui_rag(RAG_MESSAGE)

    def test_plain_question_is_not_flagged(self):
        assert not looks_like_openwebui_rag("Wie viele Zeilen hat die Tabelle?")

    def test_recovers_the_real_question(self):
        assert recover_question(RAG_MESSAGE) == "Wie viele Zeilen?"

    def test_recover_passes_plain_questions_through(self):
        assert recover_question("Wie viele Zeilen?") == "Wie viele Zeilen?"


class TestCollectFiles:
    def test_reads_every_attached_file(self, tmp_path):
        first = tmp_path / "a.csv"
        second = tmp_path / "b.csv"
        first.write_bytes(b"Artikel;Menge\nA;1\n")
        second.write_bytes(b"Artikel;Menge\nB;2\n")
        attached = [
            {"file": {"filename": "a.csv", "path": str(first), "id": "1"}},
            {"file": {"filename": "b.csv", "path": str(second), "id": "2"}},
        ]
        assert len(collect_files(attached)) == 2

    def test_skips_files_that_cannot_be_found(self):
        attached = [{"file": {"filename": "weg.csv", "path": "/nicht/da.csv", "id": "1"}}]
        assert collect_files(attached) == []


class TestPipe:
    def test_asks_for_a_file_when_none_attached(self):
        output = "".join(Pipe().pipe({"messages": [{"content": "Hallo"}]}, __files__=[]))
        assert "Datei" in output

    def test_warns_when_open_webui_rag_is_active(self, tmp_path, monkeypatch):
        path = tmp_path / "a.csv"
        path.write_bytes(b"Artikel;Menge\nA;1\n")
        pipe = Pipe()
        monkeypatch.setattr(
            "adapters.openwebui_pipe.answer", lambda *a, **k: iter(["ok"])
        )
        output = "".join(
            pipe.pipe(
                {"messages": [{"content": RAG_MESSAGE}]},
                __files__=[{"file": {"filename": "a.csv", "path": str(path), "id": "1"}}],
            )
        )
        assert "file_context" in output

    def test_pipe_is_a_sync_generator_not_async(self):
        import inspect

        assert inspect.isgeneratorfunction(Pipe.pipe)
        assert not inspect.isasyncgenfunction(Pipe.pipe)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'adapters'`

- [ ] **Step 3: Write minimal implementation**

Create `adapters/__init__.py` as an empty file, then:

```python
# adapters/openwebui_pipe.py
"""Open WebUI Pipe adapter. Contains no document logic -- that lives in faro_docs."""

import glob
import os
import re

from pydantic import BaseModel

from src.faro_docs import messages_de
from src.faro_docs.answer import answer
from src.faro_docs.ingest.router import ingest_all

_RAG_MARKERS = ("### Task:", "inline citations", "<source")
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)


def looks_like_openwebui_rag(message: str) -> bool:
    """Open WebUI rewrites the user's question when file_context is enabled.

    An invisible failure otherwise: the pipe answers a prompt the user never
    wrote, confidently and wrongly.
    """
    return sum(marker in message for marker in _RAG_MARKERS) >= 2


def recover_question(message: str) -> str:
    """Pull the real question back out of Open WebUI's template."""
    match = _USER_QUERY.search(message)
    if match:
        return match.group(1).strip()
    return message


def _read_file(info: dict) -> bytes | None:
    path = info.get("path")
    if path and os.path.isfile(path):
        with open(path, "rb") as handle:
            return handle.read()

    file_id = info.get("id", "")
    directories = [
        os.environ.get("UPLOAD_DIR", ""),
        os.environ.get("DATA_DIR", ""),
        "/app/backend/data/uploads",
        "./data/uploads",
    ]
    try:
        import open_webui

        directories.append(
            os.path.join(os.path.dirname(open_webui.__file__), "data", "uploads")
        )
    except ImportError:
        pass

    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        matches = glob.glob(os.path.join(directory, f"*{file_id}*"))
        if matches:
            with open(matches[0], "rb") as handle:
                return handle.read()
    return None


def collect_files(files: list) -> list[tuple[str, bytes]]:
    """Every attached file, not just the first."""
    collected = []
    for entry in files or []:
        info = entry.get("file", {})
        raw = _read_file(info)
        if raw is not None:
            collected.append((info.get("filename", "unbenannt"), raw))
    return collected


class Pipe:
    class Valves(BaseModel):
        MODEL: str = "qwen3.6:27b"
        OLLAMA_HOST: str = ""
        MAX_TEXT_CHARS: int = 40000

    def __init__(self):
        self.id = "faro_document_assistant"
        self.name = "FARO Dokument-Assistent"
        self.valves = self.Valves()

    def _host(self) -> str:
        if self.valves.OLLAMA_HOST:
            return self.valves.OLLAMA_HOST
        return (
            os.environ.get("OLLAMA_BASE_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://localhost:11434"
        )

    def pipe(self, body: dict, __files__: list = None, __user__: dict = None):
        message = body.get("messages", [{}])[-1].get("content", "")

        if not __files__:
            yield messages_de.KEINE_DATEI
            return

        if looks_like_openwebui_rag(message):
            yield messages_de.RAG_WARNUNG + "\n"
        question = recover_question(message)

        yield messages_de.EXTRAHIERE + "\n\n"

        files = collect_files(__files__)
        if not files:
            yield messages_de.KEINE_DATEI
            return

        documents = ingest_all(files)
        if not any(document.tables for document in documents):
            yield messages_de.KEINE_TABELLE + "\n\n"

        for chunk in answer(
            documents, question, model=self.valves.MODEL, host=self._host()
        ):
            yield chunk
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/ -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add adapters/ tests/faro_docs/test_adapter.py
git commit -m "feat: add Open WebUI adapter with RAG self-detection and all-files handling"
```

---

### Task 13: Bundle generator

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/build_bundle.py`
- Test: `tests/faro_docs/test_bundle.py`

**Interfaces:**
- Consumes: every core module and the adapter.
- Produces: `MODULE_ORDER: list[str]`; `ALLOWED_IMPORTS: frozenset[str]`; `build() -> str`; writes `openwebui/faro_document_assistant.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_bundle.py
import ast
import pathlib

from tools.build_bundle import ALLOWED_IMPORTS, BUNDLE_PATH, build


def test_bundle_is_valid_python():
    compile(build(), "bundle", "exec")


def test_bundle_has_exactly_one_pipe_class():
    tree = ast.parse(build())
    pipes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Pipe"]
    assert len(pipes) == 1


def test_bundle_has_no_internal_imports_left():
    assert "from src.faro_docs" not in build()
    assert "from adapters" not in build()


def test_bundle_only_imports_allowed_packages():
    tree = ast.parse(build())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= ALLOWED_IMPORTS, f"nicht erlaubt: {imported - ALLOWED_IMPORTS}"


def test_bundle_declares_no_pip_requirements():
    header = build()[:2000]
    assert "requirements:" not in header


def test_bundle_is_deterministic():
    assert build() == build()


def test_committed_bundle_is_up_to_date():
    """Catches hand-edits to the generated file, the drift this design removes."""
    on_disk = pathlib.Path(BUNDLE_PATH).read_text(encoding="utf-8")
    assert on_disk == build(), "Bundle veraltet — `python -m tools.build_bundle` ausführen"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_bundle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.build_bundle'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/__init__.py` as an empty file, then:

```python
# tools/build_bundle.py
"""Flatten the core library and adapter into one file for Open WebUI.

Open WebUI Functions are single self-contained files that cannot import local
packages. Hand-maintaining that copy is how the previous version drifted from
src/, so it is generated instead and a test asserts it is current.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLE_PATH = ROOT / "openwebui" / "faro_document_assistant.py"

MODULE_ORDER = [
    "src/faro_docs/model.py",
    "src/faro_docs/german.py",
    "src/faro_docs/tables.py",
    "src/faro_docs/ingest/tabular.py",
    "src/faro_docs/ingest/pdf_ingest.py",
    "src/faro_docs/ingest/router.py",
    "src/faro_docs/facts.py",
    "src/faro_docs/messages_de.py",
    "src/faro_docs/answer.py",
    "adapters/openwebui_pipe.py",
]

ALLOWED_IMPORTS = frozenset({
    # stdlib
    "csv", "glob", "io", "json", "os", "queue", "re", "threading", "time",
    "unicodedata", "dataclasses", "collections", "typing", "email", "pathlib",
    # already present in Open WebUI's environment
    "pandas", "numpy", "openpyxl", "xlrd", "fitz", "PIL", "docx", "pptx",
    "bs4", "lxml", "chardet", "charset_normalizer", "ftfy", "tabulate",
    "ollama", "pydantic", "open_webui",
})

HEADER = '''"""
title: FARO Dokument-Assistent
author: Mirza
version: {version}

GENERIERTE DATEI — NICHT VON HAND BEARBEITEN.
Erzeugt aus src/faro_docs/ durch `python -m tools.build_bundle`.
Änderungen bitte dort vornehmen und neu erzeugen.

Beantwortet Fragen zu hochgeladenen Dokumenten (PDF, Excel, CSV, Text).
Zahlen werden im Code berechnet, nicht vom Sprachmodell geschätzt.
Deutsche Zahlenformate (1.234,56), mehrere Tabellenblätter, Summenzeilen und
mehrere gleichzeitig hochgeladene Dateien werden korrekt behandelt.

WICHTIG vor dem ersten Einsatz: Für dieses Modell muss die eingebaute
Dateiverarbeitung von Open WebUI abgeschaltet werden
(Fähigkeit `file_context` = false), sonst ersetzt Open WebUI die Frage des
Benutzers durch einen eigenen Text. `python -m tools.setup_openwebui` erledigt
das. Ist sie aktiv, warnt diese Funktion im Chat selbst davor.

Benötigt keine zusätzlichen Pakete.
"""
'''

VERSION = "1.0.0"

_INTERNAL_IMPORT = re.compile(r"^\s*(from\s+(src\.faro_docs|adapters)[\w.]*\s+import|import\s+(src\.faro_docs|adapters))")
_IMPORT_LINE = re.compile(r"^(import\s+\S+|from\s+\S+\s+import\s+.+)$")


def _split_module(text: str) -> tuple[list[str], list[str]]:
    """Return (top-level import lines, body lines) with internal imports removed."""
    imports, body = [], []
    for line in text.splitlines():
        if _INTERNAL_IMPORT.match(line):
            continue
        if _IMPORT_LINE.match(line) and not line.startswith((" ", "\t")):
            imports.append(line)
        else:
            body.append(line)
    return imports, body


def build() -> str:
    seen_imports: list[str] = []
    sections: list[str] = []

    for relative in MODULE_ORDER:
        text = (ROOT / relative).read_text(encoding="utf-8")
        imports, body = _split_module(text)
        for line in imports:
            if line not in seen_imports:
                seen_imports.append(line)
        sections.append(f"# ---- {relative} ----\n" + "\n".join(body).strip() + "\n")

    return (
        HEADER.format(version=VERSION)
        + "\n"
        + "\n".join(sorted(set(seen_imports)))
        + "\n\n\n"
        + "\n\n".join(sections)
    )


def main() -> None:
    BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE_PATH.write_text(build(), encoding="utf-8")
    print(f"Geschrieben: {BUNDLE_PATH} ({len(build())} Zeichen)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Generate the bundle, then run the tests**

Run: `python -m tools.build_bundle && python -m pytest tests/faro_docs/test_bundle.py -v`
Expected: bundle written, then PASS. If `test_bundle_only_imports_allowed_packages` fails, the offending import must be removed from core code, not added to the allowlist.

- [ ] **Step 5: Verify the generated bundle actually runs**

Run:
```bash
python -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('bundle', 'openwebui/faro_document_assistant.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
docs = module.ingest_all([('a.csv', 'Artikel;Menge\nA;1.234\nB;2.500\nC;750\n'.encode())])
print('Zeilen:', docs[0].total_rows())
print('Summe:', docs[0].tables[0].frame['Menge'].sum())
assert docs[0].tables[0].frame['Menge'].sum() == 4484.0
print('OK')
"
```
Expected: `Zeilen: 3`, `Summe: 4484.0`, `OK`

- [ ] **Step 6: Commit**

```bash
git add tools/ openwebui/faro_document_assistant.py tests/faro_docs/test_bundle.py
git commit -m "feat: generate the Open WebUI bundle from the core library"
```

---

### Task 14: Open WebUI setup script

**Files:**
- Create: `tools/setup_openwebui.py`
- Test: `tests/faro_docs/test_setup_script.py`

**Interfaces:**
- Consumes: nothing from the core.
- Produces: `disable_file_context(base_url: str, token: str, model_id: str) -> dict`; `main()` CLI taking `--url`, `--email`, `--password`.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_setup_script.py
import json
from urllib.error import HTTPError

from tools.setup_openwebui import disable_file_context


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_creates_the_model_override_and_refreshes(monkeypatch):
    calls = []

    def fake_urlopen(request, *args, **kwargs):
        url = request.full_url
        calls.append(url)
        if "/models/create" in url:
            return _FakeResponse({"id": "faro_document_assistant"})
        return _FakeResponse({"data": []})

    monkeypatch.setattr("tools.setup_openwebui.urlopen", fake_urlopen)
    result = disable_file_context("http://x", "tok", "faro_document_assistant")

    assert any("/models/create" in c for c in calls)
    assert any("refresh=true" in c for c in calls), "Modell-Cache muss neu geladen werden"
    assert result["ok"]


def test_falls_back_to_update_when_entry_exists(monkeypatch):
    calls = []

    def fake_urlopen(request, *args, **kwargs):
        url = request.full_url
        calls.append(url)
        if "/models/create" in url:
            raise HTTPError(url, 401, "exists", {}, None)
        return _FakeResponse({"id": "faro_document_assistant"})

    monkeypatch.setattr("tools.setup_openwebui.urlopen", fake_urlopen)
    result = disable_file_context("http://x", "tok", "faro_document_assistant")

    assert any("/model/update" in c for c in calls)
    assert result["ok"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_setup_script.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.setup_openwebui'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/setup_openwebui.py
"""Turn off Open WebUI's built-in file RAG for this model.

Open WebUI rewrites the user's question whenever a file is attached, unless the
model declares capabilities.file_context = false. The model cache is loaded
lazily, so the change does nothing until /api/models?refresh=true is called --
that second step is not optional.
"""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODEL_ID = "faro_document_assistant"
MODEL_NAME = "FARO Dokument-Assistent"


def _post(url: str, token: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urlopen(request) as response:
        return json.loads(response.read())


def _get(url: str, token: str) -> dict:
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    with urlopen(request) as response:
        return json.loads(response.read())


def sign_in(base_url: str, email: str, password: str) -> str:
    result = _post(
        f"{base_url}/api/v1/auths/signin", "", {"email": email, "password": password}
    )
    return result["token"]


def disable_file_context(base_url: str, token: str, model_id: str = MODEL_ID) -> dict:
    payload = {
        "id": model_id,
        "name": MODEL_NAME,
        "meta": {
            "description": "Deterministische Dokumentauswertung; eingebaute Datei-RAG abgeschaltet.",
            "capabilities": {"file_context": False},
        },
        "params": {},
        "is_active": True,
    }
    try:
        _post(f"{base_url}/api/v1/models/create", token, payload)
        action = "erstellt"
    except HTTPError:
        _post(f"{base_url}/api/v1/models/model/update", token, payload)
        action = "aktualisiert"

    _get(f"{base_url}/api/models?refresh=true", token)
    return {"ok": True, "aktion": action, "model_id": model_id}


def main() -> None:
    parser = argparse.ArgumentParser(description="Open WebUI für den FARO-Assistenten einrichten")
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--model-id", default=MODEL_ID)
    args = parser.parse_args()

    token = sign_in(args.url, args.email, args.password)
    result = disable_file_context(args.url, token, args.model_id)
    print(f"file_context abgeschaltet ({result['aktion']}) für {result['model_id']}.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_setup_script.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/setup_openwebui.py tests/faro_docs/test_setup_script.py
git commit -m "feat: add setup script that disables Open WebUI built-in file RAG"
```

---

### Task 15: Golden corpus

Fixtures encode the failure modes observed in real documents. Real files dropped into `tests/corpus/real/` are picked up automatically, so genuine office documents can be added later without touching code.

**Files:**
- Create: `tests/corpus/__init__.py`
- Create: `tests/corpus/build_fixtures.py`
- Create: `tests/corpus/real/README.md`
- Test: `tests/faro_docs/test_corpus_golden.py`

**Interfaces:**
- Consumes: `ingest_one` (Task 8).
- Produces: `build_fixtures() -> dict[str, bytes]` keyed by filename; golden expectations as a module-level dict.

- [ ] **Step 1: Write the failing test**

```python
# tests/faro_docs/test_corpus_golden.py
import json
import pathlib

import pytest

from src.faro_docs.ingest.router import ingest_one
from tests.corpus.build_fixtures import EXPECTATIONS, build_fixtures

REAL_DIR = pathlib.Path(__file__).resolve().parent.parent / "corpus" / "real"


@pytest.mark.parametrize("filename", sorted(EXPECTATIONS))
def test_fixture_matches_expectation(filename):
    raw = build_fixtures()[filename]
    expected = EXPECTATIONS[filename]
    document = ingest_one(raw, filename, document_id="dok1")

    assert len(document.tables) == expected["tabellen"], f"{filename}: Tabellenzahl"
    assert document.total_rows() == expected["zeilen"], f"{filename}: Zeilenzahl"
    for column, total in expected.get("summen", {}).items():
        actual = document.tables[expected.get("tabelle_index", 0)].frame[column].sum()
        assert actual == pytest.approx(total), f"{filename}: Summe {column}"


def _real_documents():
    if not REAL_DIR.is_dir():
        return []
    return sorted(p for p in REAL_DIR.iterdir() if p.suffix == ".json")


@pytest.mark.parametrize("expectation_path", _real_documents())
def test_real_document_matches_its_sidecar(expectation_path):
    """Drop <name>.pdf plus <name>.json into tests/corpus/real/ to add a case."""
    expected = json.loads(expectation_path.read_text(encoding="utf-8"))
    document_path = next(
        p for p in expectation_path.parent.glob(expectation_path.stem + ".*")
        if p.suffix != ".json"
    )
    document = ingest_one(document_path.read_bytes(), document_path.name, "dok1")

    if "tabellen" in expected:
        assert len(document.tables) == expected["tabellen"]
    if "zeilen" in expected:
        assert document.total_rows() == expected["zeilen"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/faro_docs/test_corpus_golden.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.corpus'`

- [ ] **Step 3: Write minimal implementation**

Create `tests/corpus/__init__.py` (empty) and `tests/corpus/real/README.md`:

```markdown
# Echte Dokumente

Hier echte (gern anonymisierte) Dokumente ablegen, um sie automatisch
mitzutesten. Pro Dokument zwei Dateien:

- `rechnung_mueller.pdf` — das Dokument selbst
- `rechnung_mueller.json` — die erwarteten Werte, z. B.
  `{"tabellen": 2, "zeilen": 450}`

Die Tests finden neue Dateien von allein. Kein Code muss angefasst werden.
```

```python
# tests/corpus/build_fixtures.py
"""Fixtures reproducing the messiness observed in real German documents.

Each one encodes a failure mode that produced a wrong answer at some point:
German thousands separators, decimal commas, semicolon delimiters, cp1252
umlauts, preamble junk above the header, a Gesamt row, and several sheets.
"""

import io

import pandas as pd

EXPECTATIONS = {
    "deutsche_liste.csv": {
        "tabellen": 1,
        "zeilen": 3,
        "summen": {"Menge": 4484.0, "Betrag": 30.5},
    },
    "mit_praeambel.csv": {"tabellen": 1, "zeilen": 2},
    "mit_summenzeile.csv": {"tabellen": 1, "zeilen": 2, "summen": {"Betrag": 150.0}},
    "cp1252_umlaute.csv": {"tabellen": 1, "zeilen": 2},
    "mehrere_blaetter.xlsx": {"tabellen": 3, "zeilen": 6},
}


def build_fixtures() -> dict[str, bytes]:
    fixtures: dict[str, bytes] = {}

    fixtures["deutsche_liste.csv"] = (
        "Artikel;Menge;Betrag\n"
        "iPhone Display;1.234;12,00\n"
        "USB-C Kabel;2.500;8,50\n"
        "Schutzhülle;750;10,00\n"
    ).encode("utf-8")

    fixtures["mit_praeambel.csv"] = (
        "FARO Import-Export GmbH\n"
        "Rechnung Nr. 4711\n"
        "Kunde: Müller GmbH\n"
        "\n"
        "Artikel;Menge;Betrag\n"
        "Hülle;3;12,00\n"
        "Kabel;5;8,50\n"
    ).encode("utf-8")

    fixtures["mit_summenzeile.csv"] = (
        "Artikel;Betrag\n"
        "Hülle;100,00\n"
        "Kabel;50,00\n"
        "Gesamt;150,00\n"
    ).encode("utf-8")

    fixtures["cp1252_umlaute.csv"] = (
        "Artikel;Größe\nSchutzhülle;groß\nDisplayfolie;klein\n"
    ).encode("cp1252")

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"Artikel": ["A", "B"], "Menge": [1, 2]}).to_excel(
            writer, sheet_name="Januar", index=False
        )
        pd.DataFrame({"Artikel": ["C", "D"], "Menge": [3, 4]}).to_excel(
            writer, sheet_name="Februar", index=False
        )
        pd.DataFrame({"Artikel": ["E", "F"], "Menge": [5, 6]}).to_excel(
            writer, sheet_name="März", index=False
        )
    fixtures["mehrere_blaetter.xlsx"] = buffer.getvalue()

    return fixtures
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/faro_docs/test_corpus_golden.py -v`
Expected: PASS. `mit_summenzeile.csv` proves the Gesamt row is excluded (2 rows, sum 150 not 300); `deutsche_liste.csv` proves German thousands parse (4484, not 753.734).

- [ ] **Step 5: Commit**

```bash
git add tests/corpus/ tests/faro_docs/test_corpus_golden.py
git commit -m "test: add golden corpus covering German document failure modes"
```

---

### Task 16: Live verification and removal of superseded code

**Files:**
- Modify: `scripts/ask.py`
- Delete: `src/extractors/`, `src/tools.py`, `src/agent.py`, `openwebui/exact_count_pipe.py`
- Delete: `tests/test_tabular.py`, `tests/test_pdf.py`, `tests/test_router.py`, `tests/test_tools.py`, `tests/test_agent.py`, `tests/test_agent_e2e.py`
- Modify: `openwebui/README.md`, `START_HERE.md`

**Interfaces:**
- Consumes: everything.
- Produces: a verified, deployable repo with one generated pipe file.

- [ ] **Step 1: Point the CLI at the new core**

```python
# scripts/ask.py
"""Ask a question about a document from the command line.

Usage: python -m scripts.ask <datei> [<datei> ...] "<Frage>"
"""

import sys

from src.faro_docs.answer import answer
from src.faro_docs.ingest.router import ingest_all


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)

    *paths, question = sys.argv[1:]
    files = []
    for path in paths:
        with open(path, "rb") as handle:
            files.append((path.rsplit("/", 1)[-1], handle.read()))

    documents = ingest_all(files)
    for chunk in answer(
        documents, question, model="qwen2.5:7b", host="http://localhost:11434"
    ):
        print(chunk, end="", flush=True)
    print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Delete the superseded Phase 0 code and its tests**

```bash
git rm -r src/extractors src/tools.py src/agent.py openwebui/exact_count_pipe.py
git rm tests/test_tabular.py tests/test_pdf.py tests/test_router.py tests/test_tools.py tests/test_agent.py tests/test_agent_e2e.py
```

- [ ] **Step 3: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS, with no references to the deleted modules. If `tests/test_synthetic_data.py` imports removed code, update it to use `tests/corpus/build_fixtures.py` instead.

- [ ] **Step 4: Deploy to the local Open WebUI and verify through the UI**

```bash
python -m tools.build_bundle
python -m tools.setup_openwebui --url http://localhost:3000 --email dev@local.test --password 'LocalDevTest123!'
```

Then upload the generated `openwebui/faro_document_assistant.py` through Admin Panel → Functions (replacing the old function), activate it, and in a new chat verify each of these by hand:

1. Upload a German CSV with `1.234 / 2.500 / 750` in a Menge column. Ask *„Was ist die Summe der Menge?"* Expect **4.484**, not 753,734.
2. Upload a multi-sheet XLSX. Ask *„Wie viele Tabellenblätter gibt es?"* Expect the real count, via a `list_tables` call.
3. Upload two files at once. Ask *„Wie viele Zeilen insgesamt?"* Expect the sum across both.
4. Upload a file with a `Gesamt` row. Ask *„Wie viele Positionen?"* Expect the count excluding it, and the note saying so.
5. Ask something the document does not contain. Expect *„Das steht nicht im Dokument."*
6. Confirm no `### Task:` RAG warning appears — meaning `file_context` is correctly off.

Record the actual answers. Scripted verification passed once while the live UI was broken, so the UI check is the one that counts.

- [ ] **Step 5: Update the docs to match reality**

In `openwebui/README.md` and `START_HERE.md`: replace references to `exact_count_pipe.py` with `faro_document_assistant.py`, state that the file is generated and must not be hand-edited, and document the mandatory `setup_openwebui` step with what breaks without it.

- [ ] **Step 6: Commit and push**

```bash
git add -A
git commit -m "refactor: replace Phase 0 pipeline with generated faro_docs bundle"
git push origin main
```

---

## Self-Review Notes

**Spec coverage:** §5 architecture → Tasks 1, 13. §6 ingestion (CSV/Excel/PDF/text, all files) → Tasks 6, 7, 8. §7 German layer → Tasks 2, 3, 4. §8 tables, headers, totals → Tasks 4, 5. §9 facts, tools, routing, honesty → Tasks 9, 10, 11. §11 errors → Tasks 8, 10. §12 Open WebUI integration → Tasks 12, 14. §13 long documents → Task 11 (`build_context` truncation, visible). §14 testing → Tasks 15, 16.

**Deferred to Phase 2 by design, not omission:** OCR via vision model (§10), DOCX/PPTX/EML/HTML/image ingest (§6), dates (§7), and `get_text_section`/retrieval over long text (§9, §13) — the plan ships a visible truncation notice instead.

**Known follow-up:** `find_totals_rows` only inspects the final row for the unlabelled case. A totals row in the middle of a merged multi-document file is caught by label matching but not by arithmetic. Acceptable for Phase 1; revisit if the real corpus shows it.
