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


def test_one_malformed_row_does_not_balloon_every_column():
    """A stray unescaped quote in one free-text field (e.g. raw HTML in a
    scraped job description) can make the CSV parser split just that one
    row into far more fields than the header has. Found on a real 173-row,
    21-column export: one row parsed to 122 fields, and the old width =
    max(row lengths) logic padded every other row with over a hundred
    meaningless 'Spalte N' columns -- confirmed to fool the model into
    reading a unique-value count off empty garbage instead of the real
    'Company' column."""
    header = "Artikel,Menge,Beschreibung"
    good_rows = "\n".join(f"Teil{i},{i},normaler Text" for i in range(1, 20))
    bad_row = 'KaputtesTeil,99,"Text mit," Anführungszeichen," mittendrin"'
    raw = f"{header}\n{good_rows}\n{bad_row}\n".encode("utf-8")

    doc = ingest_csv(raw, document_id="dok1", filename="messy.csv")
    table = doc.tables[0]

    assert list(table.frame.columns) == ["Artikel", "Menge", "Beschreibung"]
    assert table.row_count() == 20
    assert table.frame["Menge"].sum() == pytest.approx(sum(range(1, 20)) + 99)
