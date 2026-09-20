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
