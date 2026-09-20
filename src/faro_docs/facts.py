"""Deterministic facts, computed in code so the model never has to do arithmetic."""

import pandas as pd

from src.faro_docs.model import Document


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
