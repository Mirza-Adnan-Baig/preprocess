"""CSV and Excel ingest. Every sheet becomes its own table."""

import csv
import io
from collections import Counter

import pandas as pd

from src.faro_docs.german import decode_text, detect_delimiter
from src.faro_docs.model import Document
from src.faro_docs.tables import build_table

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
