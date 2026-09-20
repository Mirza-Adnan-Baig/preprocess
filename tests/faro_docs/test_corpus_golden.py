import json
import pathlib

import pytest

from src.faro_docs.ingest.router import ingest_one
from tests.corpus.build_fixtures import EXPECTATIONS, build_fixtures

REAL_DIR = pathlib.Path(__file__).resolve().parent.parent / "corpus" / "real"


@pytest.mark.parametrize("filename", sorted(EXPECTATIONS))
def test_fixture_matches_expectation(filename):
    raw = build_fixtures()[filename]
    expected = EXPECTATIONS[filename]
    document = ingest_one(raw, filename, document_id="dok1")

    assert len(document.tables) == expected["tabellen"], f"{filename}: Tabellenzahl"
    assert document.total_rows() == expected["zeilen"], f"{filename}: Zeilenzahl"
    for column, total in expected.get("summen", {}).items():
        actual = document.tables[expected.get("tabelle_index", 0)].frame[column].sum()
        assert actual == pytest.approx(total), f"{filename}: Summe {column}"


def _real_documents():
    if not REAL_DIR.is_dir():
        return []
    return sorted(p for p in REAL_DIR.iterdir() if p.suffix == ".json")


@pytest.mark.parametrize("expectation_path", _real_documents())
def test_real_document_matches_its_sidecar(expectation_path):
    """Drop <name>.pdf plus <name>.json into tests/corpus/real/ to add a case."""
    expected = json.loads(expectation_path.read_text(encoding="utf-8"))
    document_path = next(
        p for p in expectation_path.parent.glob(expectation_path.stem + ".*")
        if p.suffix != ".json"
    )
    document = ingest_one(document_path.read_bytes(), document_path.name, "dok1")

    if "tabellen" in expected:
        assert len(document.tables) == expected["tabellen"]
    if "zeilen" in expected:
        assert document.total_rows() == expected["zeilen"]
