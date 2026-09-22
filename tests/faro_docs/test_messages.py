# tests/faro_docs/test_messages.py
import re

from src.faro_docs import messages_de


def test_every_german_public_string_is_german_not_english():
    """Names ending _EN are deliberately English (the RESPONSE_LANGUAGE=en path)."""
    english_giveaways = re.compile(
        r"\b(the|please|error|file|table|row|column|could not|failed)\b", re.IGNORECASE
    )
    for name in dir(messages_de):
        if name.startswith("_") or name.endswith("_EN"):
            continue
        value = getattr(messages_de, name)
        if isinstance(value, str):
            assert not english_giveaways.search(value), f"{name} contains English"


def test_every_en_public_string_is_english_not_german():
    umlaut_giveaways = re.compile(r"[äöüßÄÖÜ]")
    for name in dir(messages_de):
        if not name.endswith("_EN"):
            continue
        value = getattr(messages_de, name)
        if isinstance(value, str):
            assert not umlaut_giveaways.search(value), f"{name} contains German"


def test_footer_names_the_source_of_the_numbers():
    footer = messages_de.provenance_footer({"tabelle"}, [])
    assert "berechnet" in footer.lower()


def test_footer_includes_notes():
    footer = messages_de.provenance_footer({"text"}, ["Summenzeile ausgeschlossen"])
    assert "Summenzeile ausgeschlossen" in footer


def test_footer_is_empty_without_input():
    assert messages_de.provenance_footer(set(), []) == ""


def test_footer_can_render_in_english():
    footer = messages_de.provenance_footer({"tabelle"}, [], language="en")
    assert "computed from the table" in footer
    assert "Herkunft" not in footer


def test_footer_distinguishes_text_search_from_a_table():
    """count_text_occurrences (a Ctrl+F-style word count in free text) is
    not a table lookup -- the footer must say so distinctly, not claim
    'computed from the table' for a document that has no table at all."""
    footer = messages_de.provenance_footer({"textsuche"}, [])
    assert "textsuche" in footer.lower() or "gezählt" in footer.lower()
    assert "tabelle" not in footer.lower()


class TestTranslateNotes:
    def test_passes_through_unchanged_for_german(self):
        notes = ["Datei wurde als utf-8 gelesen."]
        assert messages_de.translate_notes(notes, "de") == notes

    def test_translates_encoding_note(self):
        result = messages_de.translate_notes(["Datei wurde als utf-8 gelesen."], "en")
        assert result == ["File was read as utf-8."]

    def test_translates_column_format_note(self):
        note = 'Spalte „Menge“: Punkt als Tausendertrennzeichen gedeutet (deutsches Format); eindeutig ist es nicht.'
        result = messages_de.translate_notes([note], "en")
        assert result == [
            'Column "Menge": dot read as a thousands separator '
            "(German format); not unambiguous."
        ]

    def test_translates_totals_excluded_note(self):
        note = (
            "Hinweis: 1 Summenzeile(n) wurde(n) von Anzahl und Summen "
            "ausgeschlossen, damit nicht doppelt gezählt wird."
        )
        result = messages_de.translate_notes([note], "en")
        assert result == [
            "Note: 1 totals row(s) were excluded from counts and sums "
            "to avoid double-counting."
        ]

    def test_unknown_note_passes_through_unchanged(self):
        note = "Irgendein neuer, nicht erkannter Hinweistext."
        assert messages_de.translate_notes([note], "en") == [note]
