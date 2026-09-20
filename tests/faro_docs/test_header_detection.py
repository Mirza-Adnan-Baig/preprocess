# tests/faro_docs/test_header_detection.py
from src.faro_docs.tables import detect_header_row


def test_finds_header_after_invoice_preamble():
    rows = [
        ["FARO Import-Export GmbH", None, None],
        ["Rechnung Nr. 4711", None, None],
        [None, None, None],
        ["Artikel", "Menge", "Einzelpreis"],
        ["iPhone Display", "3", "149,82"],
        ["USB-C Kabel", "10", "8,50"],
    ]
    assert detect_header_row(rows) == 3


def test_prefers_german_vocabulary_over_mere_text():
    rows = [
        ["Sehr geehrte Damen und Herren", "bitte beachten", "Sie folgendes"],
        ["Pos.", "Bezeichnung", "Betrag"],
        ["1", "Hülle", "12,00"],
    ]
    assert detect_header_row(rows) == 1


def test_first_row_when_already_a_header():
    rows = [["Artikel", "Menge"], ["Hülle", "3"]]
    assert detect_header_row(rows) == 0


def test_handles_empty_input():
    assert detect_header_row([]) == 0


def test_does_not_choose_a_numeric_row():
    rows = [["1", "2", "3"], ["Artikel", "Menge", "Preis"], ["Hülle", "3", "12,00"]]
    assert detect_header_row(rows) == 1
