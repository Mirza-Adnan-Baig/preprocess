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
