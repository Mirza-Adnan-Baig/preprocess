import pytest

from src.faro_docs.tables import build_table


def test_excludes_labelled_totals_row_from_count_and_sum():
    rows = [
        ["Artikel", "Menge", "Betrag"],
        ["iPhone Display", "3", "149,82"],
        ["USB-C Kabel", "10", "8,50"],
        ["Gesamt", "13", "158,32"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.row_count() == 2
    assert len(table.totals_rows) == 1
    assert table.notes  # the exclusion is stated, not silent


def test_excludes_unlabelled_row_that_equals_the_sum():
    rows = [
        ["Artikel", "Betrag"],
        ["A", "100,00"],
        ["B", "50,00"],
        ["", "150,00"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.row_count() == 2


def test_keeps_a_row_that_only_looks_similar():
    rows = [
        ["Artikel", "Betrag"],
        ["A", "100,00"],
        ["B", "50,00"],
        ["C", "70,00"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.row_count() == 3
    assert table.totals_rows == []


def test_german_amounts_sum_correctly_after_assembly():
    rows = [
        ["Artikel", "Menge"],
        ["A", "1.234"],
        ["B", "2.500"],
        ["C", "750"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.frame["Menge"].sum() == pytest.approx(4484.0)
    assert table.columns["Menge"].numeric_style == "german"


def test_records_the_numeric_decision_for_each_column():
    rows = [["Artikel", "Betrag"], ["A", "12,00"]]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.columns["Betrag"].numeric_rule
    assert table.columns["Artikel"].numeric_style == "none"


def test_drops_empty_rows_and_repeated_headers():
    rows = [
        ["Artikel", "Menge"],
        ["A", "1"],
        [None, None],
        ["Artikel", "Menge"],
        ["B", "2"],
    ]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert table.row_count() == 2


def test_names_unnamed_columns_positionally():
    rows = [["Artikel", ""], ["A", "1"]]
    table = build_table(rows, table_id="dok1:t1", label="Tabelle 1")
    assert "Spalte 2" in table.frame.columns
