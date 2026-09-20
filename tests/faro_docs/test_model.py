import pandas as pd

from src.faro_docs.model import ColumnInfo, Document, Table


def test_table_defaults_are_independent_between_instances():
    a = Table(id="dok1:tabelle1", label="Tabelle 1", frame=pd.DataFrame({"Menge": [1]}))
    b = Table(id="dok1:tabelle2", label="Tabelle 2", frame=pd.DataFrame({"Menge": [2]}))
    a.notes.append("Summenzeile ausgeschlossen")
    assert b.notes == []
    assert a.columns == {} and b.totals_rows == []


def test_document_row_total_sums_its_tables():
    doc = Document(
        id="dok1",
        filename="rechnung.pdf",
        media_type="application/pdf",
        text="",
        tables=[
            Table(id="dok1:t1", label="T1", frame=pd.DataFrame({"a": [1, 2, 3]})),
            Table(id="dok1:t2", label="T2", frame=pd.DataFrame({"a": [1, 2]})),
        ],
    )
    assert doc.total_rows() == 5


def test_column_info_records_its_decision():
    info = ColumnInfo(
        name="Betrag",
        numeric_style="german",
        numeric_rule="Dezimalkomma erkannt",
        numeric_confident=True,
    )
    assert info.numeric_style == "german"
    assert info.numeric_rule
