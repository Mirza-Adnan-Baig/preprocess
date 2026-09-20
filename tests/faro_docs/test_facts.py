import pandas as pd
import pytest

from src.faro_docs.facts import compute_facts
from src.faro_docs.model import ColumnInfo, Document, Table


def _document(doc_id, frame, table_id="t1"):
    columns = {
        name: ColumnInfo(name=name, numeric_style="german" if frame[name].dtype != object else "none")
        for name in frame.columns
    }
    return Document(
        id=doc_id,
        filename=f"{doc_id}.csv",
        media_type="text/csv",
        tables=[Table(id=f"{doc_id}:{table_id}", label="Tabelle 1", frame=frame, columns=columns)],
    )


def test_reports_row_counts_per_table():
    docs = [_document("dok1", pd.DataFrame({"Menge": [1.0, 2.0, 3.0]}))]
    facts = compute_facts(docs)
    assert facts["dok1"]["tabellen"]["dok1:t1"]["zeilen"] == 3


def test_computes_column_statistics():
    docs = [_document("dok1", pd.DataFrame({"Menge": [1.0, 2.0, 3.0]}))]
    stats = compute_facts(docs)["dok1"]["tabellen"]["dok1:t1"]["spalten"]["Menge"]
    assert stats["summe"] == pytest.approx(6.0)
    assert stats["min"] == pytest.approx(1.0)
    assert stats["max"] == pytest.approx(3.0)


def test_cross_document_total_is_computed_in_code():
    docs = [
        _document("dok1", pd.DataFrame({"Menge": [1.0] * 250})),
        _document("dok2", pd.DataFrame({"Menge": [1.0] * 200})),
    ]
    summary = compute_facts(docs)["_zusammenfassung"]
    assert summary["zeilen_gesamt"] == 450
    assert summary["dokumente"] == 2
    assert summary["tabellen"] == 2


def test_facts_are_json_safe():
    import json

    docs = [_document("dok1", pd.DataFrame({"Menge": [1.0], "Artikel": ["A"]}))]
    json.dumps(compute_facts(docs), ensure_ascii=False)


def test_empty_input_does_not_crash():
    assert compute_facts([])["_zusammenfassung"]["zeilen_gesamt"] == 0
