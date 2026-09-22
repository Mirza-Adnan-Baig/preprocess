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

from collections import Counter
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
    page_count: int = 0  # 0 when the format has no pages (CSV, Excel, text)

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
_LEADING_ZERO_INTEGER = re.compile(r"^0\d+$")

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
    if any(_LEADING_ZERO_INTEGER.match(c) for c in cleaned):
        # A plain digit string with a leading zero (an EAN/barcode, an
        # article or customer number, a German postal code...) is an
        # identifier, not a quantity. Converting "0107610691403" to a
        # number silently drops the leading zero and changes the actual
        # value -- there's no numeric style that round-trips it, so the
        # column is left as text instead, exactly like any other
        # non-numeric column.
        return "none", RULE_NONE, True

    plain_values = [c for c in cleaned if _PLAIN_INTEGER.match(c)]
    if plain_values:
        min_length = min(len(v) for v in plain_values)
        unique_ratio = len(set(plain_values)) / len(plain_values)
        # 12+ digits is EAN-13 / UPC-12 / GTIN-14 / account-number territory
        # and is never a quantity, however often a value repeats -- a real
        # catalogue legitimately lists the same EAN on several rows, and
        # requiring near-uniqueness let exactly that case slip through and
        # be turned into a float (barcodes then printed as
        # "4051805300000.0"). Between 8 and 11 digits the reading is less
        # obvious, so near-uniqueness is still required there.
        if min_length >= 12 or (min_length >= 8 and unique_ratio >= 0.8):
            # No leading zero this time, but a column of long (8+ digit),
            # near-unique plain numbers is still an identifier (EAN/GTIN,
            # barcode, IBAN-like account number), never a real quantity --
            # found on a real invoice: two different 13-digit EAN codes
            # both silently converted to the *same* float64 value once
            # rendered ("4.05181e+12" for both, genuinely indistinguishable
            # to the model), and FAKTEN computed a meaningless "average
            # barcode". A genuine Menge/Anzahl column is short and full of
            # repeats; this combination of length and near-uniqueness is
            # not.
            return "none", RULE_NONE, True
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
    seen: dict[str, int] = {}
    for position, cell in enumerate(header, start=1):
        text = "" if cell is None else str(cell).strip()
        name = text or f"Spalte {position}"
        # Two columns with the same header is normal in a real export (two
        # EAN columns, or the same header repeated after a merge). Left
        # alone, pandas hands back a DataFrame instead of a Series for that
        # name and ingestion dies with an AttributeError -- the whole file
        # fails, not just one answer. Later repeats get a numbered suffix;
        # the first keeps the original name so ordinary references work.
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        names.append(name)

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


def _normalize_row_widths(rows: list[list], delimiter: str) -> list[list]:
    """Fit every row to the table's real width, without letting one bad row
    balloon the whole table into dozens of bogus columns.

    A real-world export can have a stray unescaped quote inside one free-text
    field (an HTML job description, say), which makes the standard CSV
    parser split that single row into far more fields than the header has.
    Using that row's length as "the" table width -- the previous behaviour --
    padded every other row with dozens of meaningless "Spalte N" columns and
    fooled the model into reading unique-value counts off empty garbage
    instead of the real column. The width most rows actually agree on is
    used instead, and a too-long row has its excess trailing fields rejoined
    into the last column rather than silently dropped.
    """
    if not rows:
        return rows
    counts = Counter(len(row) for row in rows)
    # On a tie (common on a short file: a couple of single-field preamble
    # lines can tie the real header+data width), prefer the wider
    # candidate -- preamble/junk lines are consistently short, so the real
    # table's width is the larger of any tied candidates, never the smaller.
    best_count = max(counts.values())
    width = max(length for length, count in counts.items() if count == best_count)
    normalized = []
    for row in rows:
        if len(row) > width:
            row = row[: width - 1] + [delimiter.join(str(c) for c in row[width - 1 :])]
        elif len(row) < width:
            row = row + [None] * (width - len(row))
        normalized.append(row)
    return normalized


def ingest_csv(raw: bytes, document_id: str, filename: str) -> Document:
    text, encoding = decode_text(raw)
    delimiter = detect_delimiter(text)
    rows = [
        row for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    ]
    padded = _normalize_row_widths(rows, delimiter)

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


def _normalise_header_cell(cell: str) -> str:
    """Collapse the cosmetic differences between the same header re-printed
    on a later page: line breaks, repeated spaces, capitalisation."""
    return " ".join(str(cell).split()).casefold()


def ingest_pdf(raw: bytes, document_id: str, filename: str) -> Document:
    import fitz  # PyMuPDF

    document = fitz.open(stream=raw, filetype="pdf")
    try:
        page_texts: list[str] = []
        groups: dict[tuple, list[list]] = {}

        originals: dict[tuple, list] = {}
        for page in document:
            page_texts.append(page.get_text())
            for found in page.find_tables().tables:
                rows = found.extract()
                if len(rows) < 2:
                    continue
                header = [
                    "" if cell is None else str(cell).strip() for cell in rows[0]
                ]
                # Group by a normalised key, not the raw header. The same
                # table continued on page 2 of a 41-page catalogue routinely
                # re-prints its header with a line break, double space or
                # different capitalisation; keying on the raw text split one
                # logical table into a pile of near-duplicate ones, each with
                # a fraction of the rows.
                key = tuple(_normalise_header_cell(cell) for cell in header)
                groups.setdefault(key, []).extend(rows[1:])
                originals.setdefault(key, header)

        text = "\n\n".join(page_texts)
        notes: list[str] = []
        if len(text.strip()) < _MIN_TEXT_PER_PAGE * max(1, len(document)):
            notes.append(NOTE_SCAN_SUSPECTED)

        tables = []
        ordered = sorted(groups.items(), key=lambda item: -len(item[1]))
        for index, (key, body) in enumerate(ordered, start=1):
            table = build_table(
                [list(originals.get(key, key))] + body,
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
            page_count=len(document),
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
    """Never re-decide whether a column is numeric here.

    build_table (src/faro_docs/tables.py) already made that call once, per
    column, using the German-aware, identifier-aware logic in german.py --
    and converted the column's real dtype accordingly. A second, independent
    pd.to_numeric(errors="coerce") here used to disagree with that decision
    on any column of plain digit strings it didn't also recognise as an
    identifier (found on a real EAN column: correctly left as text by
    build_table, then silently averaged and min/maxed here anyway, stripping
    every leading zero in the process). Trusting the column's already-decided
    dtype keeps exactly one source of truth for "is this numeric".
    """
    if not pd.api.types.is_numeric_dtype(series):
        return {
            "summe": None,
            "min": None,
            "max": None,
            "durchschnitt": None,
            "verschiedene_werte": int(series.nunique(dropna=True)),
        }
    has_numbers = bool(series.notna().any())
    return {
        "summe": float(series.sum()) if has_numbers else None,
        "min": float(series.min()) if has_numbers else None,
        "max": float(series.max()) if has_numbers else None,
        "durchschnitt": float(series.mean()) if has_numbers else None,
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
        # Open WebUI hands back every file ever attached in a chat on every
        # turn, with no signal distinguishing "just attached" from "attached
        # several messages ago" -- this is the one thing the model can use
        # to avoid defaulting to a combined answer across a stale document.
        "zuletzt_angehaengtes_dokument": documents[-1].id if documents else None,
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
DENKT_NACH = "_(Moment, die Antwort wird vorbereitet …)_"
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
DENKT_NACH_EN = "_(One moment, preparing the answer …)_"
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
    "textsuche": "per Textsuche im Dokument gezählt",
    "text": "aus dem Dokumenttext gelesen",
    "ocr": "per Texterkennung gelesen (unsicher)",
}
_QUELLEN_EN = {
    "tabelle": "computed from the table",
    "textsuche": "counted via a text search of the document",
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
    parts = [quellen[s] for s in ("tabelle", "textsuche", "text", "ocr") if s in sources]
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
            "alle Tabellen NUR im zuletzt angehängten Dokument (normale Wahl bei "
            "einer einfachen Frage wie 'wie viele Zeilen'). Mit "
            "table='alle_dokumente' die Gesamtzahl über wirklich JEDES "
            "Dokument im Chat -- nur verwenden, wenn ausdrücklich nach allen "
            "Dokumenten zusammen gefragt wird."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {
                "type": "string",
                "description": "Tabellen-Id, 'alle' (nur neuestes Dokument), oder 'alle_dokumente' (wirklich alles)",
            },
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
    {"type": "function", "function": {
        "name": "count_matching_rows",
        "description": (
            "Zählt, wie viele Zeilen einer Tabelle in einer Spalte einen Text "
            "enthalten -- die verlässliche Wahl für \"wie viele X gibt es\" auf "
            "einer echten Tabelle (z. B. wie viele Zeilen in der Spalte "
            "„Bezeichnung“ „Zuberhol“ enthalten). Liefert die exakte "
            "Gesamtzahl, anders als find_rows, das nur eine begrenzte "
            "Vorschau zurückgibt und bei vielen Treffern zu niedrig wäre."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
            "contains": {"type": "string"},
        }, "required": ["table", "column", "contains"]},
    }},
    {"type": "function", "function": {
        "name": "count_text_occurrences",
        "description": (
            "Zählt, wie oft ein Wort oder eine Zeichenfolge im Fließtext des "
            "Dokuments vorkommt (wie eine Strg+F-Suche, Groß-/Kleinschreibung "
            "wird ignoriert). NICHT für Zeilen oder Spalten einer Tabelle -- "
            "dafür count_rows oder find_rows verwenden. Mit document='alle' "
            "wird NUR im zuletzt angehängten Dokument gesucht (normale Wahl "
            "bei einer einfachen Frage); mit document='alle_dokumente' über "
            "wirklich jedes Dokument im Chat hinweg."
        ),
        "parameters": {"type": "object", "properties": {
            "search": {"type": "string", "description": "Der gesuchte Text"},
            "document": {
                "type": "string",
                "description": "Dokument-Id, 'alle' (nur neuestes Dokument), oder 'alle_dokumente' (wirklich alles)",
            },
        }, "required": ["search", "document"]},
    }},
    {"type": "function", "function": {
        "name": "search_text",
        "description": (
            "Durchsucht den GESAMTEN Dokumenttext nach einem Begriff und gibt "
            "die Fundstellen mit Textumgebung zurück. WICHTIG: oben im Prompt "
            "steht bei langen Dokumenten nur der Anfang des Textes -- mit "
            "diesem Werkzeug kommst du an JEDE Stelle des Dokuments heran, "
            "auch an Seite 30 von 41. Immer verwenden, wenn im sichtbaren "
            "Ausschnitt nichts steht oder \"[Text gekürzt]\" erscheint, bevor "
            "du sagst, etwas stehe nicht im Dokument."
        ),
        "parameters": {"type": "object", "properties": {
            "search": {"type": "string", "description": "Gesuchter Begriff"},
            "document": {
                "type": "string",
                "description": "Dokument-Id, 'alle' (nur neuestes Dokument), oder 'alle_dokumente'",
            },
            "max_treffer": {
                "type": "integer",
                "description": "Wie viele Fundstellen zurückgegeben werden (Standard 5)",
            },
        }, "required": ["search", "document"]},
    }},
    {"type": "function", "function": {
        "name": "query_table",
        "description": (
            "Die flexible Tabellenabfrage: filtern, rechnen, sortieren. "
            "Für alles, was über einfaches Zählen hinausgeht, z. B. \"welche "
            "Artikel kosten mehr als 10 Euro\", \"was kosten alle Zuberhole "
            "zusammen\", \"die 5 teuersten Positionen\", \"welcher Artikel hat "
            "die größte Menge\", \"wie viele Zeilen haben kein Barcode\". "
            "filters verknüpft mehrere Bedingungen mit UND. Ohne aggregate "
            "kommen die passenden Zeilen zurück, mit aggregate die berechnete "
            "Zahl."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Tabellen-Id"},
            "filters": {
                "type": "array",
                "description": (
                    "Liste von Bedingungen, alle müssen zutreffen. Jede: "
                    "{\"column\": Spaltenname, \"op\": contains|equals|gt|lt|"
                    "gte|lte|empty|not_empty, \"value\": Wert}"
                ),
                "items": {"type": "object", "properties": {
                    "column": {"type": "string"},
                    "op": {"type": "string"},
                    "value": {"type": "string"},
                }},
            },
            "aggregate": {
                "type": "object",
                "description": (
                    "Optional. {\"func\": count|sum|avg|min|max|distinct, "
                    "\"column\": Spaltenname}. count braucht keine Spalte."
                ),
                "properties": {
                    "func": {"type": "string"},
                    "column": {"type": "string"},
                },
            },
            "sort_by": {"type": "string", "description": "Optional: nach dieser Spalte sortieren"},
            "sort_desc": {"type": "boolean", "description": "true = absteigend (größte zuerst)"},
            "limit": {"type": "integer", "description": "Wie viele Zeilen zurückkommen (Standard 20)"},
        }, "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "column_stats",
        "description": (
            "Überblick über eine Spalte: Anzahl Werte, wie viele verschiedene, "
            "wie viele leer, die häufigsten Werte, und bei Zahlenspalten "
            "Summe/Min/Max/Durchschnitt. Gut für \"wie viele verschiedene "
            "Artikel gibt es\", \"fehlen Werte\", \"welcher Wert kommt am "
            "häufigsten vor\"."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
        }, "required": ["table", "column"]},
    }},
    {"type": "function", "function": {
        "name": "find_duplicates",
        "description": (
            "Findet Werte, die in einer Spalte mehrfach vorkommen -- z. B. "
            "doppelte EAN-Codes oder doppelt erfasste Artikelnummern in einer "
            "Preisliste."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
            "limit": {"type": "integer", "description": "Wie viele Duplikate aufgelistet werden (Standard 20)"},
        }, "required": ["table", "column"]},
    }},
    {"type": "function", "function": {
        "name": "document_info",
        "description": (
            "Aufbau eines Dokuments: Dateiname, Dateityp, Seitenzahl bei PDF, "
            "Länge des Textes, und jede erkannte Tabelle mit Spalten und "
            "Zeilenzahl. Für \"wie viele Seiten hat das Dokument\" oder "
            "\"was ist das überhaupt für eine Datei\"."
        ),
        "parameters": {"type": "object", "properties": {
            "document": {
                "type": "string",
                "description": "Dokument-Id, 'alle' (nur neuestes Dokument), oder 'alle_dokumente'",
            },
        }, "required": ["document"]},
    }},
]

SYSTEM_PROMPT = (
    "Du beantwortest Fragen zu hochgeladenen Dokumenten. Es kann sich um "
    "alles handeln: Rechnungen, Lieferscheine, Verträge, Berichte, Listen.\n\n"
    "Regeln:\n"
    "1. Zahlen, Anzahlen und Summen NIE selbst zählen oder addieren -- auch "
    "nicht, wie oft ein Wort oder eine Textstelle im Dokument vorkommt. Rufe "
    "das passende Werkzeug auf: count_rows/sum_column für Tabellenzeilen und "
    "-spalten. Für \"wie viele X gibt es\" (z. B. wie viele Zuberholteile), "
    "wenn X in einer Tabellenspalte vorkommt (Artikel, Bezeichnung, o. Ä.), "
    "IMMER count_matching_rows auf dieser Spalte verwenden, NICHT "
    "count_text_occurrences -- eine Zeile ist ein echtes Produkt, während "
    "eine reine Textsuche durch wiederholte Kopf-/Fußzeilen auf jeder Seite "
    "oder durch Zeilenumbrüche mitten im Wort verfälscht werden kann. "
    "count_text_occurrences NUR verwenden, wenn es keine passende Tabelle "
    "gibt oder ausdrücklich nach dem Fließtext gefragt wird. Deine eigene "
    "Zählung oder Rechnung ist nicht verlässlich.\n"
    "2. Fragen nach der Anzahl der Tabellen, Blätter oder Dokumente IMMER mit "
    "list_tables beziehungsweise list_documents beantworten, nie schätzen.\n"
    "3. Eine Datei kann mehrere Tabellen enthalten, und es können mehrere "
    "Dateien hochgeladen sein. Prüfe das, bevor du eine Zahl nennst.\n"
    "4. In diesem Chat können mehrere Dokumente aus verschiedenen Nachrichten "
    "vorliegen, auch aus früheren Uploads, die nichts mehr mit der aktuellen "
    "Frage zu tun haben. count_rows mit table='alle' bezieht sich deshalb "
    "NUR auf das zuletzt angehängte Dokument (FAKTEN._zusammenfassung."
    "zuletzt_angehaengtes_dokument) -- die normale Wahl bei einer einfachen "
    "Frage wie \"wie viele Zeilen\". Nur wenn die Frage sich ausdrücklich auf "
    "mehrere Dokumente oder alle zusammen bezieht, table='alle_dokumente' "
    "verwenden oder FAKTEN._zusammenfassung.zeilen_gesamt übernehmen -- und "
    "NUR dann in der Antwort erwähnen. Bei einer einfachen Frage ohne "
    "Bezug auf \"alle\"/\"zusammen\" NICHT zusätzlich die Gesamtzahl über "
    "alle Dokumente nennen, auch wenn sie in FAKTEN sichtbar ist -- das "
    "verwirrt nur, wenn niemand danach gefragt hat.\n"
    "5. Für sum_column über mehrere Dokumente hinweg gibt es keinen "
    "Werkzeug-Modus, weil Spalten in verschiedenen Dokumenten unterschiedliche "
    "Bedeutung haben können. Frage nie mehrere Tabellen einzeln ab und "
    "addiere die Ergebnisse selbst -- das ist genau die Art von Rechnung, "
    "die nicht verlässlich ist (siehe Regel 1). Wenn eine Summe über mehrere "
    "Dokumente hinweg verlangt wird, nenne stattdessen die Summe je Dokument "
    "einzeln.\n"
    "6. Inhaltliche Fragen (Worum geht es? Wer ist der Absender? Was steht in "
    "Abschnitt 4?) direkt aus dem Dokumenttext beantworten. Für den Wert einer "
    "einzelnen Zeile oder Spalte (z. B. \"Welchen EAN-Code hat Zeile 6?\") "
    "IMMER get_row oder find_rows aufrufen -- auch wenn die Zeile oben in der "
    "Tabelle bereits sichtbar ist. Nie behaupten, ein Wert sei nicht "
    "verfügbar, ohne das Werkzeug versucht zu haben.\n"
    "6b. Der oben sichtbare Dokumenttext ist bei langen Dokumenten gekürzt "
    "(erkennbar an „[… Zeichen aus der Mitte ausgelassen …]“ oder „[… weitere "
    "Zeilen nicht angezeigt …]“). Der VOLLSTÄNDIGE Text ist trotzdem "
    "erreichbar: search_text durchsucht ihn ganz und liefert die Fundstelle "
    "mit Umgebung. Bevor du sagst, etwas stehe nicht im Dokument, IMMER "
    "zuerst search_text mit einem passenden Stichwort versuchen.\n"
    "6c. Für alles, was über einfaches Zählen hinausgeht, query_table "
    "verwenden: Filter nach Zahl (\"teurer als 10 Euro\"), gefilterte Summen "
    "(\"was kosten alle X zusammen\"), Sortieren und Top-N (\"die 5 teuersten "
    "Positionen\", \"größte Menge\"), leere Felder (op=empty). column_stats "
    "für \"wie viele verschiedene\", \"welcher Wert kommt am häufigsten vor\", "
    "\"fehlen Werte\". find_duplicates für doppelte EANs oder "
    "Artikelnummern. document_info für Seitenzahl und Aufbau der Datei.\n"
    "7. Steht die Antwort nach einer ehrlichen Suche wirklich nicht im "
    "Dokument, sage genau das (in der Sprache der Frage, z. B. „Das steht "
    "nicht im Dokument.“ auf Deutsch oder „That is not in the document.“ auf "
    "Englisch). Nichts erfinden.\n"
    "8. Antworte in der Sprache, in der die Frage gestellt wurde -- Deutsch "
    "bei einer deutschen Frage, Englisch bei einer englischen Frage, "
    "ebenso in jeder anderen Sprache. Nicht die Sprache des Dokuments "
    "annehmen, wenn die Frage in einer anderen Sprache gestellt wurde. "
    "In ganzen Sätzen, knapp.\n"
    "9. Liefert ein Werkzeugaufruf einen Fehler (ein „fehler“-Feld im "
    "Ergebnis), NIE trotzdem eine Zahl oder einen Wert erfinden oder raten. "
    "Entweder das Werkzeug mit korrigierten Argumenten erneut aufrufen, "
    "oder in der Antwort klar sagen, dass die Anfrage nicht sicher "
    "beantwortet werden konnte."
)

_FORCE_ENGLISH = (
    "\n\nOVERRIDE (takes precedence over every rule above, including rule 8): "
    "you must answer only in English, in every single reply, no matter what "
    "language the question, the document, or its content is in. Never answer "
    "in German or any other language, even partially."
)


def _system_prompt_for(response_language: str) -> str:
    if response_language == "en":
        return SYSTEM_PROMPT + _FORCE_ENGLISH
    return SYSTEM_PROMPT


def _all_tables(documents: list[Document]) -> dict:
    return {table.id: table for document in documents for table in document.tables}


def _resolve_documents(documents: list[Document], document_arg) -> list[Document]:
    """Turn a document argument into the documents it refers to.

    Mirrors count_rows's 'alle' semantics: Open WebUI hands back every file
    ever attached in a chat on every turn, so a plain question defaults to
    the most recently attached document only, and 'alle_dokumente' is the
    explicit opt-in to every one of them.
    """
    if document_arg == "alle_dokumente":
        return list(documents)
    if not document_arg or document_arg == "alle":
        return documents[-1:] if documents else []
    matches = [d for d in documents if d.id == document_arg]
    if not matches:
        raise ValueError(
            f"Unbekanntes Dokument „{document_arg}“. Gültige Dokumente: "
            f"{[d.id for d in documents]}"
        )
    return matches


def _numeric_series(table, column: str) -> pd.Series:
    """A column as real numbers, using the format already decided for it."""
    info = table.columns.get(column)
    if info is not None and info.numeric_style in {"german", "english", "integer"}:
        return to_numeric_series(table.frame[column], info.numeric_style)
    # Column was kept as text (an identifier, or mixed content). Comparing it
    # numerically is still allowed where the values happen to parse, but a
    # column with nothing numeric in it must say so rather than compare
    # everything against NaN and silently return zero matches.
    coerced = pd.to_numeric(table.frame[column], errors="coerce")
    if len(table.frame) and coerced.notna().sum() == 0:
        raise ValueError(
            f"Spalte „{column}“ enthält keine Zahlen -- ein Zahlenvergleich "
            f"ist hier nicht möglich. Für Text „contains“ oder „equals“ verwenden."
        )
    return coerced


def _check_column(table, column: str) -> str:
    """Validate a column name, naming the real ones when it's wrong."""
    if column not in table.frame.columns:
        raise ValueError(
            f"Spalte „{column}“ gibt es nicht. Vorhanden: {list(table.frame.columns)}"
        )
    return column


def _parse_threshold(value):
    """Read a comparison value written in either convention.

    The question can come from either side: a German user types 10,5 and a
    model writing English types 10.5. Trying German first read "10.5" as
    ten-thousand-five (dot as a thousands separator), so a "price over
    10.5" filter silently matched nothing and the model then invented an
    answer -- confirmed live. The separator present decides the reading.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if "," in text and "." in text:
        # Both present, so one groups and one decimates: the one appearing
        # last is the decimal separator (1.234,56 German / 1,234.56 English).
        return parse_number(
            text, "german" if text.rfind(",") > text.rfind(".") else "english"
        )
    if "," in text:
        return parse_number(text, "german")
    if "." in text:
        # A lone dot in a threshold someone typed is a decimal point far more
        # often than a German thousands separator ("price over 10.5"), so it
        # is read that way; "1.234" meaning 1234 is the accepted edge case.
        return parse_number(text, "english")
    return parse_number(text, "integer")


def _display(value) -> str:
    """Render a cell value the way it should be read back.

    A whole number living in a float column stringifies as "10000.0",
    which looks like a different value from the 10000 printed in the
    document -- misleading in a duplicate list or a most-common-values
    list, where the point is to quote the value exactly.
    """
    if isinstance(value, float) and not isinstance(value, bool):
        if pd.isna(value):
            return ""
        if float(value).is_integer():
            return str(int(value))
    return str(value)


def _column_examples(frame, column: str, count: int = 3) -> list[str]:
    values = frame[column].astype(str).str.strip()
    return [v for v in values[values.ne("")].unique()[:count]]


def _positive_int(value, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


def _require(args: dict, key: str, tool: str):
    """Read a required tool argument, or raise an error the model can act
    on. A bare args[key] KeyError's own text is just "'column'" -- useless
    for a model deciding whether to retry -- and a small model has been
    observed answering with a made-up number after exactly this kind of
    silent-looking failure instead of retrying or admitting it couldn't
    tell. An explicit, instructive message makes a correct retry likelier.
    """
    value = args.get(key)
    if value in (None, ""):
        raise ValueError(
            f"„{tool}“ wurde ohne das Pflichtfeld „{key}“ aufgerufen. "
            f"Rufe das Werkzeug erneut auf und gib „{key}“ mit an."
        )
    return value


def run_tool(name: str, args: dict, documents: list[Document]):
    if name == "count_text_occurrences":
        needle = str(_require(args, "search", name)).lower()
        scope = _resolve_documents(documents, args.get("document"))
        return sum(document.text.lower().count(needle) for document in scope)

    if name == "search_text":
        needle = str(_require(args, "search", name)).lower()
        scope = _resolve_documents(documents, args.get("document"))
        try:
            max_hits = int(args.get("max_treffer") or 5)
        except (TypeError, ValueError):
            max_hits = 5
        max_hits = max(1, min(max_hits, 20))

        hits: list[dict] = []
        total = 0
        for document in scope:
            text = document.text or ""
            lowered = text.lower()
            start = 0
            while True:
                position = lowered.find(needle, start)
                if position < 0:
                    break
                total += 1
                if len(hits) < max_hits:
                    left = max(0, position - 120)
                    right = min(len(text), position + len(needle) + 120)
                    snippet = " ".join(text[left:right].split())
                    hits.append({
                        "dokument": document.id,
                        "zeichen_position": position,
                        "auszug": ("…" if left > 0 else "") + snippet
                        + ("…" if right < len(text) else ""),
                    })
                start = position + len(needle)
        return {"treffer_gesamt": total, "stellen": hits}

    if name == "document_info":
        scope = _resolve_documents(documents, args.get("document"))
        return {
            document.id: {
                "dateiname": document.filename,
                "dateityp": document.media_type,
                "seiten": document.page_count or "keine Seiten (kein PDF)",
                "textlaenge_zeichen": len(document.text or ""),
                "tabellen": {
                    table.id: {
                        "bezeichnung": table.label,
                        "zeilen": table.row_count(),
                        "spalten": [str(c) for c in table.frame.columns],
                    }
                    for table in document.tables
                },
                "hinweise": list(document.notes),
            }
            for document in scope
        }

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

    if name == "count_rows" and args.get("table") == "alle_dokumente":
        return sum(table.row_count() for table in tables.values())

    if name == "count_rows" and args.get("table") == "alle":
        # Open WebUI hands back every file ever attached in a chat on every
        # turn, with no signal telling the pipe which are newly attached vs.
        # attached several messages ago -- a plain "how many rows" question
        # after uploading a new file must not silently fold in an older,
        # no-longer-relevant document. 'alle' therefore scopes to the most
        # recently attached document only; 'alle_dokumente' above is the
        # explicit escape hatch for a genuine cross-document question.
        newest = documents[-1] if documents else None
        return sum(table.row_count() for table in (newest.tables if newest else []))

    table_id = args.get("table")
    if table_id not in tables:
        raise ValueError(
            f"Unbekannte Tabelle „{table_id}“. Gültige Tabellen: {list(tables)}"
        )
    table = tables[table_id]

    if name == "count_rows":
        return table.row_count()

    if name == "sum_column":
        column = _check_column(table, _require(args, "column", name))
        numeric = _numeric_series(table, column)
        # Say what was summed, not just the number. Confirmed live: asked
        # what one group of parts costs, a model called this tool with no
        # filter and reported the whole table's total as that group's total
        # (660 instead of 108). The number wasn't invented -- its scope was
        # mislabelled -- so the scope now travels with the number.
        return {
            "spalte": column,
            "summe": float(numeric.sum()),
            "zeilen_einbezogen": int(table.row_count()),
            "hinweis": (
                f"Summe über ALLE {table.row_count()} Zeilen der Tabelle, ohne "
                "Filter. Wenn nur bestimmte Zeilen gemeint sind (z. B. nur ein "
                "Artikeltyp), stattdessen query_table mit filters verwenden -- "
                "diese Zahl wäre sonst falsch beschriftet."
            ),
        }

    if name == "get_row":
        return table.frame.iloc[int(_require(args, "index", name))].to_dict()

    if name in {"find_rows", "count_matching_rows"}:
        column = _require(args, "column", name)
        if column not in table.frame.columns:
            raise ValueError(
                f"Spalte „{column}“ gibt es nicht. Vorhanden: {list(table.frame.columns)}"
            )
        if "contains" not in args:
            _require(args, "contains", name)  # raises, naming the missing field
        needle = str(args.get("contains") or "").lower().strip()
        # Searching for the *text* "nan"/"leer" is an attempt to find empty
        # cells, and it silently finds nothing: an empty cell is missing
        # data, not the word "nan" (pandas keeps it missing through
        # astype(str), so the match fails). Confirmed live: a model asked
        # "how many rows have no barcode", searched for "nan", got 0, and
        # concluded every row had one -- while two genuinely did not.
        if needle in {"nan", "none", "null", "leer", "empty", ""}:
            raise ValueError(
                "Leere Felder lassen sich so nicht finden. Dafür query_table "
                f"verwenden: filters=[{{\"column\": \"{column}\", \"op\": \"empty\"}}] "
                "(oder \"not_empty\" für gefüllte Felder)."
            )
        mask = table.frame[column].astype(str).str.lower().str.contains(needle, na=False)
        if name == "count_matching_rows":
            return int(mask.sum())
        return table.frame[mask].head(50).to_dict(orient="records")

    if name == "column_stats":
        column = _check_column(table, _require(args, "column", name))
        values = table.frame[column]
        filled = values[values.astype(str).str.strip().ne("") & values.notna()]
        stats = {
            "zeilen_gesamt": int(len(values)),
            "gefuellt": int(len(filled)),
            "leer": int(len(values) - len(filled)),
            "verschiedene_werte": int(filled.nunique()),
            "haeufigste_werte": [
                {"wert": _display(value), "anzahl": int(count)}
                for value, count in filled.value_counts().head(5).items()
            ],
        }
        info = table.columns.get(column)
        if info is not None and info.numeric_style in {"german", "english", "integer"}:
            numbers = to_numeric_series(values, info.numeric_style).dropna()
            if len(numbers):
                stats["zahlen"] = {
                    "summe": float(numbers.sum()),
                    "min": float(numbers.min()),
                    "max": float(numbers.max()),
                    "durchschnitt": float(numbers.mean()),
                }
        else:
            stats["hinweis"] = (
                "Textspalte (z. B. Bezeichnung oder eine Kennnummer wie EAN) -- "
                "bewusst nicht als Zahl behandelt, deshalb keine Summe."
            )
        return stats

    if name == "find_duplicates":
        column = _check_column(table, _require(args, "column", name))
        limit = _positive_int(args.get("limit"), default=20, maximum=100)
        values = table.frame[column].map(_display).str.strip()
        filled = values[values.ne("")]
        counts = filled.value_counts()
        duplicated = counts[counts > 1]
        return {
            "spalte": column,
            "werte_mit_mehrfachvorkommen": int(len(duplicated)),
            "betroffene_zeilen_gesamt": int(duplicated.sum()),
            "beispiele": [
                {"wert": _display(value), "anzahl": int(count)}
                for value, count in duplicated.head(limit).items()
            ],
        }

    if name == "query_table":
        frame = table.frame
        mask = pd.Series(True, index=frame.index)
        applied: list[str] = []
        for condition in args.get("filters") or []:
            if not isinstance(condition, dict):
                continue
            column = _check_column(table, _require(condition, "column", name))
            operator = str(condition.get("op") or "contains").lower()
            raw_value = condition.get("value")
            column_values = frame[column]

            if operator in {"empty", "not_empty"}:
                is_empty = column_values.isna() | column_values.astype(str).str.strip().eq("")
                condition_mask = is_empty if operator == "empty" else ~is_empty
            elif operator in {"contains", "equals"}:
                text = str("" if raw_value is None else raw_value).lower()
                as_text = column_values.astype(str).str.lower().str.strip()
                condition_mask = (
                    as_text.str.contains(text, na=False, regex=False)
                    if operator == "contains"
                    else as_text.eq(text)
                )
            elif operator in {"gt", "lt", "gte", "lte"}:
                numbers = _numeric_series(table, column)
                threshold = _parse_threshold(raw_value)
                if threshold is None:
                    raise ValueError(
                        f"„{raw_value}“ ist keine Zahl, mit der sich vergleichen lässt."
                    )
                comparison = {
                    "gt": numbers > threshold,
                    "lt": numbers < threshold,
                    "gte": numbers >= threshold,
                    "lte": numbers <= threshold,
                }[operator]
                condition_mask = comparison.fillna(False)
            else:
                raise ValueError(
                    f"Unbekannter Operator „{operator}“. Möglich: contains, "
                    "equals, gt, lt, gte, lte, empty, not_empty."
                )
            mask &= condition_mask
            applied.append(
                f"{column} {operator}"
                if operator in {"empty", "not_empty"}
                else f"{column} {operator} {raw_value}"
            )

        selected = frame[mask]

        # A filter that matches nothing is the most common way one of these
        # calls goes wrong -- usually op=equals against a descriptive column
        # where only part of the text was given ("Zuberhol" vs "Zuberhol Akku
        # Typ 3"). Confirmed live: the old error blamed the column for having
        # no numbers, which is not what went wrong, and the model gave up and
        # invented a total instead of retrying. Say what actually happened and
        # show real values from the column so the next call can be corrected.
        if applied and not len(selected):
            # Work out whether a partial match would have found something and
            # say so concretely -- "op='contains' would return 12 rows" is a
            # far stronger correction than advice, and it costs one pass over
            # the column to compute.
            retry_counts = {}
            for condition in args.get("filters") or []:
                if not isinstance(condition, dict):
                    continue
                target = condition.get("column")
                if target not in frame.columns or condition.get("value") in (None, ""):
                    continue
                if str(condition.get("op") or "").lower() != "equals":
                    continue
                would_match = int(
                    frame[target].astype(str).str.lower()
                    .str.contains(str(condition["value"]).lower(), na=False, regex=False)
                    .sum()
                )
                if would_match:
                    retry_counts[target] = would_match

            hinweis = (
                "Kein Treffer. KEINE Zahl erfinden -- den Aufruf korrigieren "
                "und erneut aufrufen."
            )
            if retry_counts:
                spalten = ", ".join(
                    f"„{column}“ ({count} Zeilen)" for column, count in retry_counts.items()
                )
                hinweis = (
                    f"Kein Treffer mit op='equals'. Mit op='contains' gäbe es "
                    f"Treffer in {spalten}. Rufe query_table JETZT erneut auf, "
                    f"mit op='contains' statt 'equals'. KEINE Zahl erfinden."
                )
            return {
                "treffer_gesamt": 0,
                "filter": applied,
                "hinweis": hinweis,
                "beispielwerte": {
                    condition["column"]: _column_examples(frame, condition["column"])
                    for condition in (args.get("filters") or [])
                    if isinstance(condition, dict)
                    and condition.get("column") in frame.columns
                },
            }

        aggregate = args.get("aggregate")
        if isinstance(aggregate, dict) and aggregate.get("func"):
            function = str(aggregate["func"]).lower()
            if function == "count":
                return {"filter": applied, "anzahl": int(len(selected))}
            column = _check_column(table, _require(aggregate, "column", name))
            if function == "distinct":
                return {
                    "filter": applied,
                    "verschiedene_werte": int(selected[column].astype(str).nunique()),
                }
            numbers = _numeric_series(table, column)[mask].dropna()
            if not len(numbers):
                raise ValueError(
                    f"In Spalte „{column}“ stehen bei den gefilterten Zeilen "
                    f"keine auswertbaren Zahlen. Beispielwerte: "
                    f"{_column_examples(frame, column)}"
                )
            result = {
                "sum": float(numbers.sum()),
                "avg": float(numbers.mean()),
                "min": float(numbers.min()),
                "max": float(numbers.max()),
            }.get(function)
            if result is None:
                raise ValueError(
                    f"Unbekannte Funktion „{function}“. Möglich: count, sum, "
                    "avg, min, max, distinct."
                )
            payload = {"filter": applied, "spalte": column, function: result}
            if not applied:
                # Same trap as sum_column: an aggregate with no filter is the
                # whole table, and a model asked about one group of parts has
                # been seen reporting that as the group's figure.
                payload["zeilen_einbezogen"] = int(len(frame))
                payload["hinweis"] = (
                    f"Ohne Filter gerechnet, also über ALLE {len(frame)} Zeilen. "
                    "Wenn nach einer bestimmten Gruppe gefragt wurde (z. B. ein "
                    "Artikeltyp), erneut aufrufen mit filters, sonst ist diese "
                    "Zahl falsch beschriftet."
                )
            return payload

        sort_by = args.get("sort_by")
        if sort_by:
            sort_column = _check_column(table, sort_by)
            info = table.columns.get(sort_column)
            descending = bool(args.get("sort_desc"))
            if info is not None and info.numeric_style in {"german", "english", "integer"}:
                order = to_numeric_series(selected[sort_column], info.numeric_style)
                selected = selected.loc[
                    order.sort_values(ascending=not descending, na_position="last").index
                ]
            else:
                selected = selected.sort_values(sort_column, ascending=not descending)

        limit = _positive_int(args.get("limit"), default=20, maximum=100)
        return {
            "filter": applied,
            "treffer_gesamt": int(len(selected)),
            "zeilen": selected.head(limit).to_dict(orient="records"),
        }

    raise ValueError(f"Unbekanntes Werkzeug: {name}")


def _trim_text(text: str, max_text_chars: int) -> str:
    """Keep the beginning AND the end of a long document, not just the start.

    A 41-page invoice or catalogue puts the sender, date and document
    numbers at the very beginning and the totals, tax lines, payment terms
    and signatures at the very end -- head-only truncation threw the entire
    second half away, so "what's the total?" on a long document was
    unanswerable from the text even though it's one of the most likely
    questions. The middle is where the repetitive line items live, and
    those are reachable through the table tools and search_text anyway.
    """
    if len(text) <= max_text_chars:
        return text
    head_chars = int(max_text_chars * 0.7)
    tail_chars = max_text_chars - head_chars
    omitted = len(text) - max_text_chars
    return (
        text[:head_chars]
        + f"\n\n…[{omitted} Zeichen aus der Mitte ausgelassen — mit search_text "
        "ist der vollständige Text durchsuchbar]…\n\n"
        + text[-tail_chars:]
    )


def _table_markdown(frame, max_rows: int = 200) -> str:
    """Table preview keeping the first and last rows of a long table.

    Same reasoning as _trim_text: a totals or summary row sits at the
    bottom, and head-only preview hid it on any table longer than the cap.
    """
    if len(frame) <= max_rows:
        return frame.to_markdown(index=False)
    head_rows = int(max_rows * 0.7)
    tail_rows = max_rows - head_rows
    omitted = len(frame) - max_rows
    return (
        frame.head(head_rows).to_markdown(index=False)
        + f"\n\n…[{omitted} weitere Zeilen nicht angezeigt — Zählen, Summieren "
        "und Suchen laufen über die Werkzeuge immer über ALLE Zeilen]…\n\n"
        + frame.tail(tail_rows).to_markdown(index=False)
    )


def build_context(documents: list[Document], max_text_chars: int = 40000) -> str:
    """Document text, table markdown, and code-computed facts."""
    parts = []
    for index, document in enumerate(documents):
        text = _trim_text(document.text or "", max_text_chars)
        marker = (
            " (zuletzt angehängt)" if len(documents) > 1 and index == len(documents) - 1 else ""
        )
        parts.append(f"## Dokument {document.id}: {document.filename}{marker}\n{text}")
        for table in document.tables:
            parts.append(
                f"### {table.id} — {table.label} "
                f"(Spalten: {', '.join(str(c) for c in table.frame.columns)}, "
                f"Zeilen: {table.row_count()})\n"
                + _table_markdown(table.frame)
            )
    parts.append(
        "FAKTEN: " + json.dumps(compute_facts(documents), ensure_ascii=False, default=str)
    )
    return "\n\n".join(parts)


_HEARTBEAT = object()
_DONE = object()
# Renders as nothing in Markdown/HTML, so repeated keep-alive ticks after the
# first one stay invisible to the reader while still putting bytes on the
# wire -- Open WebUI/the browser can otherwise treat a long silent gap during
# Ollama's prefill as a dead connection and drop it (see _stream_with_heartbeat).
_KEEPALIVE = "​"


def _stream_with_heartbeat(client, model, messages, tools, interval, num_ctx):
    """Ollama prefills a long document before emitting anything; a silent gap
    that long drops the browser connection, so emit a heartbeat while waiting."""
    channel: queue.Queue = queue.Queue()

    def worker():
        try:
            for chunk in client.chat(
                model=model, messages=messages, tools=tools, stream=True,
                options={"num_ctx": num_ctx},
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
    heartbeat_interval: float = 0.5,
    num_ctx: int = 16384,
) -> Iterator[str]:
    """Plain sync generator -- async pipes never signal completion (open-webui#20196,
    confirmed still present in a real 0.11.3 install: an async pipe's final
    message does arrive, but the UI's "Stop" button never clears and the
    chat is stuck looking like it's still generating).

    response_language: "" (default) lets the model match whatever language
    the question was asked in, and keeps this pipe's own fixed messages in
    German. Set to "en" to force English everywhere -- the model's answer,
    this pipe's own status/error messages, and the notes ingestion produces
    -- for local testing by someone who doesn't read German. The real
    office deployment leaves this at its default.

    heartbeat_interval: how often (seconds) to poll for output while Ollama
    prefills. Open WebUI (functions.py) iterates this generator with a
    plain synchronous `for` loop inside an async function, so every single
    poll genuinely blocks its *entire* event loop -- not just this chat's
    request -- for up to this many seconds at a time (confirmed by reading
    Open WebUI's own source). A long prefill is many such polls back to
    back, which is why the browser's own websocket can show "connection
    lost, reconnecting" for the whole wait even though the request itself
    is fine and completes correctly. Kept short so each individual freeze
    is brief enough that it usually doesn't trip the websocket's own
    timeout, rather than a few long freezes that reliably do. Also tunable
    for tests, to run them fast.

    num_ctx: Ollama's context window in tokens, passed explicitly on every
    request. Ollama silently defaults an unconfigured model to 4096 tokens
    regardless of what the model itself supports (confirmed via `ollama ps`)
    -- a real document's extracted text plus its table markdown plus FAKTEN
    can exceed that on a table with a couple hundred rows, at which point
    Ollama quietly drops the *oldest* part of the prompt to fit, and the
    model answers confidently from a table it never actually saw in full.
    16384 comfortably covers the default MAX_TEXT_CHARS (40000 chars); raise
    both together if MAX_TEXT_CHARS is raised.
    """
    import ollama

    client = ollama.Client(host=host)
    context = build_context(documents, max_text_chars=max_text_chars)
    messages = [
        {"role": "system", "content": _system_prompt_for(response_language)},
        {"role": "user", "content": f"DOKUMENTE:\n{context}\n\nFRAGE: {question}"},
    ]
    # Not gated on _all_tables(documents): count_text_occurrences works on a
    # document's free text and needs no table at all -- a pure-text upload
    # (no extractable table) must still get tools, not be silently limited
    # to the model's own unreliable reading-based counting.
    tools = TOOL_SCHEMAS if documents else None
    used_tool_names: set[str] = set()

    for _ in range(max_rounds):
        content = ""
        tool_calls = None
        seen_output = False
        heartbeat_shown = False
        try:
            for item in _stream_with_heartbeat(
                client, model, messages, tools,
                interval=heartbeat_interval, num_ctx=num_ctx,
            ):
                if item is _HEARTBEAT:
                    if not seen_output:
                        if not heartbeat_shown:
                            template = (
                                DENKT_NACH_EN if response_language == "en" else DENKT_NACH
                            )
                            yield template + "\n\n"
                            heartbeat_shown = True
                        else:
                            yield _KEEPALIVE
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
            sources = set()
            if used_tool_names & {"count_text_occurrences"}:
                sources.add("textsuche")
            if used_tool_names - {"count_text_occurrences", "list_documents"}:
                sources.add("tabelle")
            if not sources:
                sources = {"text"}
            notes = [n for d in documents for n in d.notes]
            notes += [n for d in documents for t in d.tables for n in t.notes]
            notes = translate_notes(notes, response_language or "de")
            yield provenance_footer(sources, notes, language=response_language or "de")
            return

        for call in tool_calls:
            name = call["function"]["name"]
            used_tool_names.add(name)
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
        NUM_CTX: int = 16384

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
            num_ctx=self.valves.NUM_CTX,
        ):
            yield chunk
