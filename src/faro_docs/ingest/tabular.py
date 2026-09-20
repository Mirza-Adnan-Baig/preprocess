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
