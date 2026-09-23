"""CSV and Excel under realistic mess.

PDF got most of the attention while these two were only lightly covered,
even though a price list or stock export arrives as CSV/Excel far more
often than as a PDF. Everything here is a shape a real export actually
produces: BOMs, Windows line endings, quoted delimiters, a preamble
above the header, German decimals, leading-zero codes stored as text,
totals rows, blank rows and columns, several sheets at once.
"""

import io

import pandas as pd
import pytest

from src.faro_docs.answer import run_tool
from src.faro_docs.ingest.tabular import ingest_csv, ingest_excel


def _table(doc):
    return doc.tables[0]


class TestCsvRealWorldShapes:
    def test_utf8_bom_does_not_corrupt_the_first_header(self):
        """Excel's "CSV UTF-8" export writes a BOM. If it lands in the
        first header cell, every later reference to that column fails."""
        raw = "﻿Artikel;Menge\nAkku;3\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="bom.csv")
        assert list(_table(doc).frame.columns) == ["Artikel", "Menge"]

    def test_windows_line_endings(self):
        raw = "Artikel;Menge\r\nAkku;3\r\nDisplay;2\r\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="crlf.csv")
        assert _table(doc).row_count() == 2

    def test_comma_delimiter_with_german_decimals_in_quotes(self):
        """A comma-separated export from a German system quotes its
        decimal commas -- splitting on the wrong character would shred
        every row."""
        raw = 'Artikel,Preis\n"Akku","4,86"\n"Display","10,93"\n'.encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="komma.csv")
        table = _table(doc)
        assert list(table.frame.columns) == ["Artikel", "Preis"]
        assert table.frame["Preis"].sum() == pytest.approx(15.79)

    def test_tab_separated(self):
        raw = "Artikel\tMenge\nAkku\t3\nDisplay\t2\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="tabs.csv")
        assert list(_table(doc).frame.columns) == ["Artikel", "Menge"]

    def test_quoted_field_containing_the_delimiter(self):
        raw = 'Artikel;Bezeichnung;Menge\nA1;"Akku, gross";3\n'.encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="quoted.csv")
        assert _table(doc).frame.iloc[0]["Bezeichnung"] == "Akku, gross"

    def test_preamble_above_the_header_is_skipped(self):
        raw = (
            "faro IMPORT EXPORT GmbH\n"
            "Preisliste Stand 13.05.2026\n"
            "\n"
            "Artikelnr;Bezeichnung;Einzelpreis\n"
            "33447;Akku;4,86\n"
            "32965;Display;10,93\n"
        ).encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="preamble.csv")
        table = _table(doc)
        assert list(table.frame.columns) == ["Artikelnr", "Bezeichnung", "Einzelpreis"]
        assert table.row_count() == 2

    def test_blank_rows_between_data_are_dropped(self):
        raw = "Artikel;Menge\nAkku;3\n\n\nDisplay;2\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="luecken.csv")
        assert _table(doc).row_count() == 2

    def test_cp1252_umlauts_survive(self):
        raw = "Artikel;Menge\nGehäuse für Hülle;3\n".encode("cp1252")
        doc = ingest_csv(raw, document_id="dok1", filename="umlaut.csv")
        assert "Gehäuse für Hülle" in _table(doc).frame.iloc[0]["Artikel"]

    def test_leading_zero_codes_stay_exact_and_are_searchable(self):
        raw = (
            "Artikel;EAN\n"
            "Akku;0405180533447\n"
            "Display;4051805329656\n"
        ).encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="ean.csv")
        table = _table(doc)
        assert table.frame.iloc[0]["EAN"] == "0405180533447"
        found = run_tool(
            "find_rows",
            {"table": table.id, "column": "EAN", "contains": "0405180533447"},
            [doc],
        )
        assert len(found) == 1

    def test_a_totals_row_is_excluded_from_the_count(self):
        raw = (
            "Artikel;Betrag\n"
            "Akku;4,86\n"
            "Display;10,93\n"
            "Gesamt;15,79\n"
        ).encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="summe.csv")
        table = _table(doc)
        assert table.row_count() == 2, "the Gesamt row must not count as an item"
        assert table.frame["Betrag"].sum() == pytest.approx(15.79)

    def test_single_column_file_still_works(self):
        raw = "EAN\n4051805334476\n4051805329656\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="eine_spalte.csv")
        assert _table(doc).row_count() == 2

    def test_empty_file_does_not_crash(self):
        doc = ingest_csv(b"", document_id="dok1", filename="leer.csv")
        assert doc.tables == []

    def test_header_only_file_does_not_crash(self):
        doc = ingest_csv(b"Artikel;Menge\n", document_id="dok1", filename="nurkopf.csv")
        assert doc.total_rows() == 0

    def test_duplicate_column_headers_do_not_kill_the_whole_file(self):
        """Two columns with the same header is normal in a real export.
        Left alone, pandas returns a DataFrame instead of a Series for
        that name and ingestion died with an AttributeError -- the entire
        file failed, not just one answer. Found by probing, not in the
        wild, but it would have been a hard failure on a real file."""
        raw = "EAN;Artikel;EAN\n0111;Akku;222\n333;Display;444\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="dup.csv")
        table = _table(doc)
        assert list(table.frame.columns) == ["EAN", "Artikel", "EAN (2)"]
        assert table.row_count() == 2
        # and both columns stay individually addressable
        assert len(run_tool(
            "find_rows", {"table": table.id, "column": "EAN (2)", "contains": "444"}, [doc]
        )) == 1

    def test_german_dates_are_not_mangled_into_numbers(self):
        raw = "Artikel;Datum\nAkku;13.05.2026\nDisplay;01.06.2026\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="datum.csv")
        table = _table(doc)
        assert table.columns["Datum"].numeric_style == "none"
        assert table.frame.iloc[0]["Datum"] == "13.05.2026"

    def test_currency_symbols_in_cells_still_sum(self):
        raw = "Artikel;Preis\nAkku;4,86 €\nDisplay;10,93 €\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="waehrung.csv")
        assert _table(doc).frame["Preis"].sum() == pytest.approx(15.79)

    def test_negative_amounts_including_parentheses(self):
        """A credit note writes -4,86; some systems write (10,93)."""
        raw = "Artikel;Betrag\nGutschrift;-4,86\nStorno;(10,93)\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="minus.csv")
        assert _table(doc).frame["Betrag"].tolist() == pytest.approx([-4.86, -10.93])

    def test_percentages_are_not_treated_as_plain_numbers(self):
        raw = "Artikel;MwSt\nAkku;19%\nDisplay;7%\n".encode("utf-8")
        doc = ingest_csv(raw, document_id="dok1", filename="prozent.csv")
        assert _table(doc).columns["MwSt"].numeric_style == "none"


def _workbook(sheets: dict) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return buffer.getvalue()


class TestExcelRealWorldShapes:
    def test_every_sheet_becomes_its_own_queryable_table(self):
        raw = _workbook({
            "Januar": pd.DataFrame({"Artikel": ["Akku", "Display"], "Menge": [3, 2]}),
            "Februar": pd.DataFrame({"Artikel": ["Kabel"], "Menge": [7]}),
        })
        doc = ingest_excel(raw, document_id="dok1", filename="umsatz.xlsx")
        assert len(doc.tables) == 2
        totals = {t.id: t.row_count() for t in doc.tables}
        assert sorted(totals.values()) == [1, 2]

    def test_numbers_stored_as_real_excel_numbers_are_summable(self):
        raw = _workbook({"Preise": pd.DataFrame({
            "Artikel": ["Akku", "Display"], "Einzelpreis": [4.86, 10.93],
        })})
        doc = ingest_excel(raw, document_id="dok1", filename="preise.xlsx")
        table = _table(doc)
        result = run_tool(
            "sum_column", {"table": table.id, "column": "Einzelpreis"}, [doc]
        )
        assert result["summe"] == pytest.approx(15.79)

    def test_german_decimals_stored_as_text_still_sum(self):
        """Plenty of exports write numbers as text like "4,86"."""
        raw = _workbook({"Preise": pd.DataFrame({
            "Artikel": ["Akku", "Display"], "Einzelpreis": ["4,86", "10,93"],
        })})
        doc = ingest_excel(raw, document_id="dok1", filename="text_preise.xlsx")
        table = _table(doc)
        assert table.frame["Einzelpreis"].sum() == pytest.approx(15.79)

    def test_leading_zero_code_column_stays_text(self):
        raw = _workbook({"Artikel": pd.DataFrame({
            "Bezeichnung": ["Akku", "Display"],
            "EAN": ["0405180533447", "4051805329656"],
        })})
        doc = ingest_excel(raw, document_id="dok1", filename="ean.xlsx")
        assert _table(doc).frame.iloc[0]["EAN"] == "0405180533447"

    def test_blank_rows_and_columns_are_dropped(self):
        frame = pd.DataFrame({
            "Artikel": ["Akku", None, "Display"],
            "Leer": [None, None, None],
            "Menge": [3, None, 2],
        })
        doc = ingest_excel(_workbook({"Daten": frame}), document_id="dok1", filename="x.xlsx")
        table = _table(doc)
        assert "Leer" not in table.frame.columns
        assert table.row_count() == 2

    def test_query_tools_work_the_same_on_an_excel_sheet(self):
        """Whatever works on a CSV has to work on a sheet -- same tools,
        same behaviour, no format-specific surprises."""
        raw = _workbook({"Katalog": pd.DataFrame({
            "Bezeichnung": ["Zubehör Akku", "LCD Display", "Zubehör Kabel"],
            "Einzelpreis": ["4,86", "10,93", "2,50"],
        })})
        doc = ingest_excel(raw, document_id="dok1", filename="katalog.xlsx")
        table = _table(doc)
        result = run_tool("query_table", {
            "table": table.id,
            "filters": [{"column": "Bezeichnung", "op": "contains", "value": "Zubehör"}],
            "aggregate": {"func": "sum", "column": "Einzelpreis"},
        }, [doc])
        assert result["sum"] == pytest.approx(7.36)

    def test_sheet_with_a_preamble_above_the_header(self):
        frame = pd.DataFrame({
            "A": ["faro IMPORT EXPORT", "Preisliste", "Artikel", "Akku", "Display"],
            "B": [None, None, "Menge", 3, 2],
        })
        doc = ingest_excel(_workbook({"Blatt1": frame}), document_id="dok1", filename="p.xlsx")
        table = _table(doc)
        assert "Artikel" in [str(c) for c in table.frame.columns]
        assert table.row_count() == 2
