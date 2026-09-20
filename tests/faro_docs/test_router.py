import io

import pandas as pd

from src.faro_docs.ingest.router import detect_kind, ingest_all, ingest_one


def _xlsx_bytes():
    buffer = io.BytesIO()
    pd.DataFrame({"Artikel": ["A"], "Menge": [1]}).to_excel(buffer, index=False)
    return buffer.getvalue()


class TestDetection:
    def test_detects_by_magic_bytes_not_just_extension(self):
        assert detect_kind("falsch_benannt.csv", _xlsx_bytes()) == "excel"
        assert detect_kind("falsch_benannt.xlsx", b"%PDF-1.7\n...") == "pdf"

    def test_detects_csv(self):
        assert detect_kind("liste.csv", b"Artikel;Menge\nA;1\n") == "csv"

    def test_unknown_type(self):
        assert detect_kind("zeichnung.dwg", b"\x00\x01\x02binary") == "unknown"


class TestIngestAll:
    def test_processes_every_file_not_only_the_first(self):
        files = [
            ("a.csv", "Artikel;Menge\nA;1\n".encode()),
            ("b.csv", "Artikel;Menge\nB;2\nC;3\n".encode()),
        ]
        documents = ingest_all(files)
        assert len(documents) == 2
        assert sum(doc.total_rows() for doc in documents) == 3

    def test_document_ids_are_unique_and_tables_namespaced(self):
        files = [("a.csv", b"Artikel;Menge\nA;1\n"), ("b.csv", b"Artikel;Menge\nB;2\n")]
        documents = ingest_all(files)
        ids = {doc.id for doc in documents}
        assert len(ids) == 2
        table_ids = {t.id for doc in documents for t in doc.tables}
        assert len(table_ids) == 2

    def test_one_bad_file_does_not_stop_the_others(self):
        files = [
            ("kaputt.pdf", b"nicht wirklich ein PDF"),
            ("gut.csv", "Artikel;Menge\nA;1\n".encode()),
        ]
        documents = ingest_all(files)
        assert len(documents) == 2
        broken = [d for d in documents if d.filename == "kaputt.pdf"][0]
        assert broken.notes  # German explanation, no crash

    def test_unknown_type_is_reported_in_german(self):
        documents = ingest_all([("zeichnung.dwg", b"\x00\x01binary")])
        assert documents[0].notes
        assert documents[0].tables == []
