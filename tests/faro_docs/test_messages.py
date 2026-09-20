# tests/faro_docs/test_messages.py
import re

from src.faro_docs import messages_de


def test_every_public_string_is_german_not_english():
    english_giveaways = re.compile(
        r"\b(the|please|error|file|table|row|column|could not|failed)\b", re.IGNORECASE
    )
    for name in dir(messages_de):
        if name.startswith("_"):
            continue
        value = getattr(messages_de, name)
        if isinstance(value, str):
            assert not english_giveaways.search(value), f"{name} contains English"


def test_footer_names_the_source_of_the_numbers():
    footer = messages_de.provenance_footer({"tabelle"}, [])
    assert "berechnet" in footer.lower()


def test_footer_includes_notes():
    footer = messages_de.provenance_footer({"text"}, ["Summenzeile ausgeschlossen"])
    assert "Summenzeile ausgeschlossen" in footer


def test_footer_is_empty_without_input():
    assert messages_de.provenance_footer(set(), []) == ""
