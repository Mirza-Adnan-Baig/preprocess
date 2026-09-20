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
