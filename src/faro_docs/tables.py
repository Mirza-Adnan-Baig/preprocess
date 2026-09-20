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
