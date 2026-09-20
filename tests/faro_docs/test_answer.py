# tests/faro_docs/test_answer.py
import pandas as pd
import pytest

from src.faro_docs.answer import TOOL_SCHEMAS, build_context, run_tool
from src.faro_docs.model import ColumnInfo, Document, Table


def _documents():
    frame_a = pd.DataFrame({"Artikel": ["A", "B"], "Menge": [1.0, 2.0]})
    frame_b = pd.DataFrame({"Artikel": ["C"], "Menge": [3.0]})
    return [
        Document(
            id="dok1", filename="a.csv", media_type="text/csv", text="FARO GmbH",
            tables=[Table(id="dok1:t1", label="Tabelle 1", frame=frame_a,
                          columns={"Menge": ColumnInfo("Menge", "german", "", True)})],
        ),
        Document(
            id="dok2", filename="b.csv", media_type="text/csv", text="Lagerhaus Müller",
            tables=[Table(id="dok2:t1", label="Tabelle 1", frame=frame_b)],
        ),
    ]


class TestTools:
    def test_list_documents_names_every_file(self):
        result = run_tool("list_documents", {}, _documents())
        assert set(result) == {"dok1", "dok2"}

    def test_list_tables_reports_ids_and_row_counts(self):
        result = run_tool("list_tables", {}, _documents())
        assert result["dok1:t1"]["zeilen"] == 2

    def test_count_rows_for_one_table(self):
        assert run_tool("count_rows", {"table": "dok1:t1"}, _documents()) == 2

    def test_count_rows_alle_scopes_to_most_recently_attached_document(self):
        """Open WebUI hands back every file ever attached in a chat on every
        turn, with no way to tell 'just attached' apart from 'attached three
        messages ago' -- 'alle' must not silently fold an older, no-longer-
        relevant document into a plain 'how many rows' question."""
        assert run_tool("count_rows", {"table": "alle"}, _documents()) == 1

    def test_count_rows_alle_dokumente_is_the_real_cross_document_total(self):
        assert run_tool("count_rows", {"table": "alle_dokumente"}, _documents()) == 3

    def test_count_rows_alle_with_one_document_is_that_document(self):
        assert run_tool("count_rows", {"table": "alle"}, _documents()[:1]) == 2

    def test_sum_column(self):
        assert run_tool("sum_column", {"table": "dok1:t1", "column": "Menge"},
                        _documents()) == pytest.approx(3.0)

    def test_sum_of_non_numeric_column_raises_rather_than_returning_zero(self):
        with pytest.raises(ValueError):
            run_tool("sum_column", {"table": "dok1:t1", "column": "Artikel"}, _documents())

    def test_unknown_table_names_the_valid_ids(self):
        with pytest.raises(ValueError) as excinfo:
            run_tool("count_rows", {"table": "gibtsnicht"}, _documents())
        assert "dok1:t1" in str(excinfo.value)

    def test_get_row(self):
        row = run_tool("get_row", {"table": "dok1:t1", "index": 0}, _documents())
        assert row["Artikel"] == "A"

    def test_find_rows_filters(self):
        rows = run_tool("find_rows", {"table": "dok1:t1", "column": "Artikel",
                                      "contains": "B"}, _documents())
        assert len(rows) == 1 and rows[0]["Artikel"] == "B"


class TestSchemas:
    def test_every_schema_requires_an_explicit_table_except_the_listers(self):
        for schema in TOOL_SCHEMAS:
            function = schema["function"]
            if function["name"] in {"list_documents", "list_tables"}:
                continue
            assert "table" in function["parameters"]["required"]


class TestContext:
    def test_context_contains_text_tables_and_facts(self):
        context = build_context(_documents())
        assert "FARO GmbH" in context
        assert "Lagerhaus Müller" in context
        assert "dok1:t1" in context
        assert "zeilen_gesamt" in context

    def test_long_text_is_truncated_visibly_not_silently(self):
        documents = _documents()
        documents[0].text = "x" * 100_000
        context = build_context(documents, max_text_chars=1000)
        assert len(context) < 60_000
        assert "gekürzt" in context.lower()
