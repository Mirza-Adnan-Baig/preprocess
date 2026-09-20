"""
title: FARO Dokument-Assistent
author: Mirza
version: 1.0.0

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

from collections.abc import Iterable
from collections.abc import Iterator
from dataclasses import dataclass, field
from pydantic import BaseModel
import csv
import glob
import io
import json
import os
import queue
import re
import threading
import time
import unicodedata


# ---- src/faro_docs/model.py ----
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


# ---- src/faro_docs/german.py ----
"""German-first parsing: numbers, encodings, delimiters.

Every user and every document is German. German writes 1.234,56 where English
writes 1,234.56, which means a naive pd.to_numeric turns 1.234 into the float
1.234 and throws away 12,00 as "not a number". Both failures are silent and
produce confidently wrong totals, so format is decided once per column from all
its values -- never per cell -- and the decision is recorded so any number can
be explained afterwards.
"""


import pandas as pd

_SPACES = "    "
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
    # A single stray numeric-looking cell in an otherwise textual column
    # (e.g. one part number inside a Bezeichnung/Artikel column) must not
    # convert the whole column, silently turning every real description
    # into NaN. Require a majority of the non-empty values to actually
    # look numeric before assigning any numeric style at all.
    if recognised == 0 or recognised * 2 <= len(cleaned):
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


# ---- src/faro_docs/tables.py ----
"""Table discovery: finding headers, removing totals rows, building Tables."""

import pandas as pd


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


def _is_index_like(series: pd.Series) -> bool:
    """A plain running count (1, 2, 3, ... in order) is a line/position
    number, never a genuine accumulating quantity -- without excluding it,
    a sequential Pos. column trivially satisfies a totals-row's arithmetic
    pattern by coincidence (e.g. 3 == 1+2) on virtually any short table.
    """
    values = series.tolist()
    return values == [float(i) for i in range(1, len(values) + 1)]


def _is_constant(series: pd.Series) -> bool:
    """A column that never changes (a flat VAT rate, a repeated unit
    price) can never arithmetically agree with a running total, and must
    not be allowed to block detecting a genuine total in a column that
    actually does accumulate.
    """
    non_null = [v for v in series.tolist() if v is not None and not pd.isna(v)]
    return len(non_null) > 0 and len(set(non_null)) == 1


def find_totals_rows(frame: pd.DataFrame, columns: dict[str, ColumnInfo]) -> list[int]:
    """Rows that restate a total rather than adding data.

    Counting a "Gesamt" row is a silent doubling and the most likely wrong
    answer on a real invoice, so both labelled and unlabelled totals are found.

    KNOWN LIMITATION: requiring every surviving ("evidential") column to
    agree only gives real corroboration when those columns are actually
    independent. On an invoice billed at a uniform unit price (a common
    shape: hourly/daily-rate services, or goods all priced the same),
    Betrag is just Menge times a constant, so once the constant price
    column is excluded, Menge and Betrag move together -- if the last
    line item's quantity happens to equal the sum of the quantities above
    it, both "agree" by construction and a genuine line item is wrongly
    excluded as a total. Measured on synthetic invoices: roughly 4% of
    uniform-unit-price tables with a quantity column trigger this; tables
    with varying prices, or a single evidential column whose value simply
    coincides with a round-number sum, do so far more rarely (well under
    1%). This is a real, measured gap, not a hidden one -- closing it
    needs the last line item's description cell to be checked too (blank
    on a genuine total, filled on a real line item), which a fix round
    already tried and reverted after it broke on tables with a second,
    incidental text column; a version that reintroduces that signal more
    carefully is future work, not attempted here.
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

    if last >= 2 and last not in flagged and numeric_columns:
        evidential_columns = []
        for name in numeric_columns:
            series = to_numeric_series(frame[name], columns[name].numeric_style)
            if _is_index_like(series) or _is_constant(series):
                continue
            evidential_columns.append(name)

        checkable_matches = []
        for name in evidential_columns:
            series = to_numeric_series(frame[name], columns[name].numeric_style)
            candidate = series.iloc[last]
            if candidate is None or pd.isna(candidate):
                continue
            preceding = series.iloc[:last].sum()
            if not preceding:
                continue
            checkable_matches.append(
                _matches_totals_tolerance(float(candidate), float(preceding))
            )

        # Every column that actually accumulates must independently agree
        # this looks like a total -- a genuine totals row's real monetary
        # columns (Betrag, a summed Menge, etc.) all restate the sum above
        # them, so requiring full agreement among just the columns capable
        # of providing real evidence is strict without being blind to
        # multi-column corroboration.
        if checkable_matches and all(checkable_matches):
            flagged.add(last)

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


# ---- src/faro_docs/ingest/tabular.py ----
"""CSV and Excel ingest. Every sheet becomes its own table."""


import pandas as pd


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


# ---- src/faro_docs/ingest/pdf_ingest.py ----
"""PDF ingest. Keeps every distinct table and merges ones split across pages."""


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


# ---- src/faro_docs/ingest/router.py ----
"""Dispatch uploads by sniffed content, and process every file."""


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
    if suffix == "pdf":
        return "pdf"
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


# ---- src/faro_docs/facts.py ----
"""Deterministic facts, computed in code so the model never has to do arithmetic."""

import pandas as pd



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


# ---- src/faro_docs/messages_de.py ----
"""Every fixed string the pipe itself emits.

German by default, since the real users at the office are non-technical
Germans. A RESPONSE_LANGUAGE valve (see adapters/openwebui_pipe.py) can
force everything -- these fixed strings, the note translations below, and
the model's own answer via the system prompt -- into English instead, for
local testing by someone who doesn't read German.
"""


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

KEINE_DATEI_EN = "Please attach a file to your question (PDF, Excel, CSV, or text)."
EXTRAHIERE_EN = "_(analyzing document …)_"
DENKT_NACH_EN = "_(still working, {sekunden}s …)_"
KEINE_TABELLE_EN = (
    "No usable table was found in this file. "
    "Exact counts or sums can't be answered reliably as a result."
)
OLLAMA_NICHT_ERREICHBAR_EN = (
    "The language model is not reachable at `{host}`. "
    "Please check the OLLAMA_HOST setting under Admin Panel > Functions."
)
ZU_VIELE_RUNDEN_EN = (
    "I couldn't reach a final answer within the allowed steps. "
    "Please phrase the question a bit more precisely."
)
RAG_WARNUNG_EN = (
    "**Warning: Open WebUI's built-in file processing is active.**\n"
    "It replaces your question with its own text before this function ever "
    "sees it, which makes answers unreliable.\n"
    "Fix in the admin area: disable the `file_context` capability for this model.\n"
)

_QUELLEN = {
    "tabelle": "aus der Tabelle berechnet",
    "text": "aus dem Dokumenttext gelesen",
    "ocr": "per Texterkennung gelesen (unsicher)",
}
_QUELLEN_EN = {
    "tabelle": "computed from the table",
    "text": "read from the document text",
    "ocr": "read via text recognition (uncertain)",
}

# The small, fixed vocabulary of German note templates produced deep in
# ingestion (src/faro_docs/german.py, tables.py, ingest/*.py) -- translated
# here, in one place, rather than threading a language parameter through
# every extraction function for a handful of known strings.
_RULE_TRANSLATIONS = {
    "Dezimalkomma erkannt (deutsches Format)": "decimal comma detected (German format)",
    "Dezimalpunkt erkannt (englisches Format)": "decimal point detected (English format)",
    "Punkt als Tausendertrennzeichen gedeutet (deutsches Format); eindeutig ist es nicht": (
        "dot read as a thousands separator (German format); not unambiguous"
    ),
    "Punkt als Dezimaltrennzeichen gedeutet (englisches Format); eindeutig ist es nicht": (
        "dot read as a decimal separator (English format); not unambiguous"
    ),
    "Gemischte Zahlenformate in derselben Spalte": "mixed number formats in the same column",
    "Ganze Zahlen ohne Trennzeichen": "whole numbers with no separators",
    "Keine Zahlenspalte": "not a numeric column",
}

_NOTE_PATTERNS_EN = [
    (
        re.compile(r'^Spalte „(?P<name>.+)“: (?P<rule>.+)\.$'),
        lambda m: f'Column "{m["name"]}": '
        f'{_RULE_TRANSLATIONS.get(m["rule"], m["rule"])}.',
    ),
    (
        re.compile(r"^Datei wurde als (?P<encoding>.+) gelesen\.$"),
        lambda m: f'File was read as {m["encoding"]}.',
    ),
    (
        re.compile(
            r"^Hinweis: (?P<count>\d+) Summenzeile\(n\) wurde\(n\) von Anzahl und "
            r"Summen ausgeschlossen, damit nicht doppelt gezählt wird\.$"
        ),
        lambda m: f'Note: {m["count"]} totals row(s) were excluded from counts '
        "and sums to avoid double-counting.",
    ),
    (
        re.compile(
            r"^Dateityp „(?P<suffix>.+)“ wird nicht unterstützt\. Unterstützt "
            r"werden derzeit PDF, Excel \(XLSX/XLS\), CSV und Textdateien\.$"
        ),
        lambda m: f'File type "{m["suffix"]}" is not supported. Currently '
        "supported: PDF, Excel (XLSX/XLS), CSV, and text files.",
    ),
    (
        re.compile(r"^Die Datei „(?P<filename>.+)“ konnte nicht gelesen werden: (?P<error>.+)$"),
        lambda m: f'The file "{m["filename"]}" could not be read: {m["error"]}',
    ),
    (
        re.compile(
            r"^Diese Datei enthält kaum auslesbaren Text und ist vermutlich ein "
            r"Scan\. Texterkennung ist in dieser Version noch nicht aktiv\.$"
        ),
        lambda m: "This file contains almost no extractable text and is "
        "likely a scan. Text recognition isn't active in this version yet.",
    ),
]


def translate_notes(notes: list[str], language: str) -> list[str]:
    """Translate the fixed-template notes ingestion produces, if language != 'de'.

    Free-form text embedded in a note (a raw exception message, a filename)
    is left as-is -- only the surrounding German template is translated.
    """
    if language == "de":
        return notes
    translated = []
    for note in notes:
        for pattern, render in _NOTE_PATTERNS_EN:
            match = pattern.match(note)
            if match:
                translated.append(render(match))
                break
        else:
            translated.append(note)
    return translated


def provenance_footer(sources: set[str], notes: list[str], language: str = "de") -> str:
    """One short line saying where the answer came from, in the given language."""
    quellen = _QUELLEN if language == "de" else _QUELLEN_EN
    parts = [quellen[s] for s in ("tabelle", "text", "ocr") if s in sources]
    if not parts and not notes:
        return ""
    lines = []
    if parts:
        label = "_Herkunft: " if language == "de" else "_Source: "
        lines.append(label + ", ".join(parts) + "._")
    lines.extend(f"_{note}_" for note in notes)
    return "\n\n" + "\n".join(lines)


# ---- src/faro_docs/answer.py ----
"""Tools the model may call, and the loop that runs them."""


import pandas as pd


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
    "6. Steht die Antwort nicht im Dokument, sage genau das (in der Sprache "
    "der Frage, z. B. „Das steht nicht im Dokument.“ auf Deutsch oder "
    "„That is not in the document.“ auf Englisch). Nichts erfinden.\n"
    "7. Antworte in der Sprache, in der die Frage gestellt wurde -- Deutsch "
    "bei einer deutschen Frage, Englisch bei einer englischen Frage, "
    "ebenso in jeder anderen Sprache. Nicht die Sprache des Dokuments "
    "annehmen, wenn die Frage in einer anderen Sprache gestellt wurde. "
    "In ganzen Sätzen, knapp."
)

_FORCE_ENGLISH = (
    "\n\nOVERRIDE: always answer in English, regardless of the language "
    "the question was asked in. This overrides rule 7 above."
)


def _system_prompt_for(response_language: str) -> str:
    if response_language == "en":
        return SYSTEM_PROMPT + _FORCE_ENGLISH
    return SYSTEM_PROMPT


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
    max_text_chars: int = 40000,
    response_language: str = "",
) -> Iterator[str]:
    """Plain sync generator -- async pipes never signal completion (open-webui#20196).

    response_language: "" (default) lets the model match whatever language
    the question was asked in, and keeps this pipe's own fixed messages in
    German. Set to "en" to force English everywhere -- the model's answer,
    this pipe's own status/error messages, and the notes ingestion produces
    -- for local testing by someone who doesn't read German. The real
    office deployment leaves this at its default.
    """
    import ollama

    client = ollama.Client(host=host)
    context = build_context(documents, max_text_chars=max_text_chars)
    messages = [
        {"role": "system", "content": _system_prompt_for(response_language)},
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
                        template = (
                            DENKT_NACH_EN if response_language == "en" else DENKT_NACH
                        )
                        yield template.format(
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
            template = (
                OLLAMA_NICHT_ERREICHBAR_EN if response_language == "en" else OLLAMA_NICHT_ERREICHBAR
            )
            yield "\n\n" + template.format(host=host)
            yield f"\n\n_({error})_"
            return

        messages.append(
            {"role": "assistant", "content": content, "tool_calls": tool_calls}
        )
        if not tool_calls:
            sources = {"tabelle"} if used_tools else {"text"}
            notes = [n for d in documents for n in d.notes]
            notes += [n for d in documents for t in d.tables for n in t.notes]
            notes = translate_notes(notes, response_language or "de")
            yield provenance_footer(sources, notes, language=response_language or "de")
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

    yield "\n\n" + (ZU_VIELE_RUNDEN_EN if response_language == "en" else ZU_VIELE_RUNDEN)


# ---- adapters/openwebui_pipe.py ----
"""Open WebUI Pipe adapter. Contains no document logic -- that lives in faro_docs."""




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
        RESPONSE_LANGUAGE: str = ""

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
        forced_english = self.valves.RESPONSE_LANGUAGE == "en"

        if not __files__:
            yield KEINE_DATEI_EN if forced_english else KEINE_DATEI
            return

        if looks_like_openwebui_rag(message):
            yield (RAG_WARNUNG_EN if forced_english else RAG_WARNUNG) + "\n"
        question = recover_question(message)

        yield (EXTRAHIERE_EN if forced_english else EXTRAHIERE) + "\n\n"

        files = collect_files(__files__)
        if not files:
            yield KEINE_DATEI_EN if forced_english else KEINE_DATEI
            return

        documents = ingest_all(files)
        if not any(document.tables for document in documents):
            yield (KEINE_TABELLE_EN if forced_english else KEINE_TABELLE) + "\n\n"

        for chunk in answer(
            documents,
            question,
            model=self.valves.MODEL,
            host=self._host(),
            max_text_chars=self.valves.MAX_TEXT_CHARS,
            response_language=self.valves.RESPONSE_LANGUAGE,
        ):
            yield chunk
