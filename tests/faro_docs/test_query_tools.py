"""The tools added after walking through what a real user actually asks.

Every question type in docs/question-coverage.md that isn't a plain count
lands in one of these: filtered sums, numeric filters, top-N, duplicates,
missing values, page counts, and -- most importantly -- search_text,
which reaches parts of a long document the model never sees directly.
"""

import pandas as pd
import pytest

from src.faro_docs.answer import _parse_threshold, build_context, run_tool
from src.faro_docs.model import ColumnInfo, Document, Table


class TestThresholdParsing:
    """A comparison value arrives in whichever convention the asker uses:
    a German user types 10,5 and a model writing English types 10.5.
    Reading German first turned "10.5" into 105 (dot as thousands
    separator), so "price over 10.5" silently matched nothing and the
    model then invented an answer -- confirmed live."""

    @pytest.mark.parametrize("value,expected", [
        ("10.5", 10.5),
        ("10,5", 10.5),
        ("10", 10.0),
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        (10.5, 10.5),
    ])
    def test_reads_both_conventions(self, value, expected):
        assert _parse_threshold(value) == pytest.approx(expected)

    def test_nonsense_returns_none_rather_than_guessing(self):
        assert _parse_threshold("abc") is None


def _catalogue():
    """A small parts catalogue shaped like the real one: article number,
    description, barcode (one missing), price in German format, quantity."""
    frame = pd.DataFrame({
        "Artikelnr": ["33447", "32965", "31002", "30500"],
        "Bezeichnung": [
            "Zuberhol Akku iPhone 7",
            "LCD + Touch iPhone 7",
            "Zuberhol Kabel USB-C",
            "Display iPhone 12",
        ],
        "Barcode": ["4051805334476", "4051805329656", "", "4051805334476"],
        "Einzelpreis": [4.86, 10.93, 2.50, 25.00],
        "Menge": [1.0, 3.0, 10.0, 1.0],
    })
    columns = {
        "Artikelnr": ColumnInfo("Artikelnr", "integer", "", True),
        "Bezeichnung": ColumnInfo("Bezeichnung", "none", "", True),
        "Barcode": ColumnInfo("Barcode", "none", "", True),
        "Einzelpreis": ColumnInfo("Einzelpreis", "german", "", True),
        "Menge": ColumnInfo("Menge", "integer", "", True),
    }
    return [
        Document(
            id="dok1", filename="katalog.pdf", media_type="application/pdf",
            text="faro IMPORT EXPORT GmbH\nRechnung 26-126920\nGesamtbetrag 43,29 EUR",
            page_count=41,
            tables=[Table(id="dok1:t1", label="Tabelle 1", frame=frame, columns=columns)],
        )
    ]


class TestQueryTable:
    def test_filtered_sum(self):
        """'What do all the Zuberhol parts cost together?' -- the single
        most likely real question on a parts catalogue, and one that no
        earlier tool could answer."""
        result = run_tool("query_table", {
            "table": "dok1:t1",
            "filters": [{"column": "Bezeichnung", "op": "contains", "value": "Zuberhol"}],
            "aggregate": {"func": "sum", "column": "Einzelpreis"},
        }, _catalogue())
        assert result["sum"] == pytest.approx(7.36)

    def test_numeric_filter_count(self):
        result = run_tool("query_table", {
            "table": "dok1:t1",
            "filters": [{"column": "Einzelpreis", "op": "gt", "value": "5"}],
            "aggregate": {"func": "count"},
        }, _catalogue())
        assert result["anzahl"] == 2

    def test_german_number_threshold_is_understood(self):
        """A German user types 10,93 -- not 10.93."""
        result = run_tool("query_table", {
            "table": "dok1:t1",
            "filters": [{"column": "Einzelpreis", "op": "gte", "value": "10,93"}],
            "aggregate": {"func": "count"},
        }, _catalogue())
        assert result["anzahl"] == 2

    def test_top_n_by_price(self):
        result = run_tool("query_table", {
            "table": "dok1:t1", "sort_by": "Einzelpreis", "sort_desc": True, "limit": 2,
        }, _catalogue())
        assert [row["Einzelpreis"] for row in result["zeilen"]] == [25.00, 10.93]
        assert result["treffer_gesamt"] == 4

    def test_empty_finds_the_missing_barcode(self):
        result = run_tool("query_table", {
            "table": "dok1:t1",
            "filters": [{"column": "Barcode", "op": "empty"}],
            "aggregate": {"func": "count"},
        }, _catalogue())
        assert result["anzahl"] == 1

    def test_several_filters_are_combined_with_and(self):
        result = run_tool("query_table", {
            "table": "dok1:t1",
            "filters": [
                {"column": "Bezeichnung", "op": "contains", "value": "Zuberhol"},
                {"column": "Einzelpreis", "op": "lt", "value": "3"},
            ],
            "aggregate": {"func": "count"},
        }, _catalogue())
        assert result["anzahl"] == 1

    def test_comparing_a_text_column_numerically_says_so(self):
        with pytest.raises(ValueError) as excinfo:
            run_tool("query_table", {
                "table": "dok1:t1",
                "filters": [{"column": "Bezeichnung", "op": "gt", "value": "5"}],
            }, _catalogue())
        assert "keine Zahlen" in str(excinfo.value)

    def test_zero_matches_explains_itself_instead_of_blaming_the_column(self):
        """op=equals against a descriptive column is the most common way
        one of these calls goes wrong ("Zuberhol" vs "Zuberhol Akku
        iPhone 7"). Confirmed live: the old error claimed the column had
        no numbers -- not what went wrong -- and the model gave up and
        invented a total. The result must name the real problem and show
        real values so the next call can be corrected."""
        result = run_tool("query_table", {
            "table": "dok1:t1",
            "filters": [{"column": "Bezeichnung", "op": "equals", "value": "Zuberhol"}],
            "aggregate": {"func": "sum", "column": "Einzelpreis"},
        }, _catalogue())
        assert result["treffer_gesamt"] == 0
        assert "contains" in result["hinweis"]
        assert any(
            "Zuberhol Akku" in value
            for value in result["beispielwerte"]["Bezeichnung"]
        )

    def test_unknown_operator_lists_the_valid_ones(self):
        with pytest.raises(ValueError) as excinfo:
            run_tool("query_table", {
                "table": "dok1:t1",
                "filters": [{"column": "Menge", "op": "ungefaehr", "value": "5"}],
            }, _catalogue())
        assert "contains" in str(excinfo.value)


class TestColumnStats:
    def test_reports_distinct_empty_and_most_common(self):
        stats = run_tool("column_stats", {"table": "dok1:t1", "column": "Barcode"}, _catalogue())
        assert stats["leer"] == 1
        assert stats["gefuellt"] == 3
        assert stats["verschiedene_werte"] == 2  # one barcode appears twice
        assert stats["haeufigste_werte"][0]["anzahl"] == 2

    def test_numeric_column_gets_numeric_summary(self):
        stats = run_tool("column_stats", {"table": "dok1:t1", "column": "Einzelpreis"}, _catalogue())
        assert stats["zahlen"]["summe"] == pytest.approx(43.29)

    def test_identifier_column_says_why_it_has_no_sum(self):
        stats = run_tool("column_stats", {"table": "dok1:t1", "column": "Barcode"}, _catalogue())
        assert "zahlen" not in stats
        assert "hinweis" in stats


class TestFindDuplicates:
    def test_finds_the_repeated_barcode(self):
        """A real catalogue export had the same EAN on three different
        rows -- worth being able to ask about directly."""
        result = run_tool("find_duplicates", {"table": "dok1:t1", "column": "Barcode"}, _catalogue())
        assert result["werte_mit_mehrfachvorkommen"] == 1
        assert result["beispiele"][0]["wert"] == "4051805334476"
        assert result["beispiele"][0]["anzahl"] == 2

    def test_no_duplicates_reports_zero_rather_than_failing(self):
        result = run_tool("find_duplicates", {"table": "dok1:t1", "column": "Artikelnr"}, _catalogue())
        assert result["werte_mit_mehrfachvorkommen"] == 0


class TestDocumentInfo:
    def test_reports_page_count_and_structure(self):
        info = run_tool("document_info", {"document": "alle"}, _catalogue())["dok1"]
        assert info["seiten"] == 41
        assert info["tabellen"]["dok1:t1"]["zeilen"] == 4


class TestSearchText:
    def test_reaches_text_far_beyond_what_the_model_can_see(self):
        """The whole point: on a long document the model only ever sees a
        trimmed extract, but search_text runs over the complete text, so
        something on page 41 is still reachable."""
        filler = "Fuellmaterial Zeile. " * 6000
        documents = [Document(
            id="dok1", filename="lang.pdf", media_type="application/pdf",
            text=filler + "\nGesamtbetrag: 18,79 EUR\n", page_count=41,
        )]
        context = build_context(documents, max_text_chars=40000)
        assert len(documents[0].text) > 100_000

        result = run_tool("search_text", {"search": "Gesamtbetrag", "document": "alle"}, documents)
        assert result["treffer_gesamt"] == 1
        assert "18,79" in result["stellen"][0]["auszug"]
        assert result["stellen"][0]["zeichen_position"] > 40000  # past the visible cap

    def test_returns_surrounding_context_not_just_a_count(self):
        result = run_tool("search_text", {"search": "Rechnung", "document": "alle"}, _catalogue())
        assert "26-126920" in result["stellen"][0]["auszug"]

    def test_hit_count_is_capped_but_total_is_honest(self):
        documents = [Document(
            id="dok1", filename="x.txt", media_type="text/plain", text="Akku " * 50,
        )]
        result = run_tool("search_text", {
            "search": "Akku", "document": "alle", "max_treffer": 3,
        }, documents)
        assert result["treffer_gesamt"] == 50
        assert len(result["stellen"]) == 3


class TestLongDocumentContext:
    def test_context_keeps_the_end_of_a_long_document_not_only_the_start(self):
        """A 41-page invoice puts the sender at the top and the total at
        the very bottom -- head-only truncation made 'what is the total?'
        unanswerable from the text."""
        documents = [Document(
            id="dok1", filename="lang.pdf", media_type="application/pdf",
            text=("Zeile. " * 20000) + "\nGesamtbetrag 18,79 EUR",
        )]
        context = build_context(documents, max_text_chars=40000)
        assert "Gesamtbetrag 18,79 EUR" in context
        assert "ausgelassen" in context

    def test_long_table_preview_keeps_first_and_last_rows(self):
        frame = pd.DataFrame({"Pos": list(range(1, 501)), "Wert": ["x"] * 499 + ["LETZTE"]})
        documents = [Document(
            id="dok1", filename="lang.csv", media_type="text/csv",
            tables=[Table(id="dok1:t1", label="Tabelle 1", frame=frame)],
        )]
        context = build_context(documents)
        assert "LETZTE" in context
        assert "nicht angezeigt" in context


class TestIdentifierAndDisplayHandling:
    def _catalogue_with_repeated_barcodes(self):
        frame = pd.DataFrame({
            "Artikelnr": ["10000", "10001", "10002", "10003"],
            "Barcode": ["4051805300000", "4051805300000", "4051805300001", ""],
        })
        return [Document(
            id="dok1", filename="k.csv", media_type="text/csv",
            tables=[Table(id="dok1:t1", label="Tabelle 1", frame=frame)],
        )]

    def test_thirteen_digit_codes_stay_text_even_when_they_repeat(self):
        """A real catalogue legitimately lists the same EAN on several
        rows. Requiring near-uniqueness let exactly that case through and
        turned barcodes into floats, printing them as
        '4051805300000.0' -- confirmed live on a generated catalogue
        whose uniqueness ratio landed just under the old threshold."""
        from src.faro_docs.german import detect_numeric_format
        style, _, _ = detect_numeric_format([
            "4051805300000", "4051805300000", "4051805300001", "4051805300002",
        ])
        assert style == "none"

    def test_whole_numbers_are_quoted_without_a_trailing_point_zero(self):
        frame = pd.DataFrame({"Nr": [10000.0, 10000.0, 10001.0]})
        documents = [Document(
            id="dok1", filename="k.csv", media_type="text/csv",
            tables=[Table(id="dok1:t1", label="T", frame=frame)],
        )]
        result = run_tool("find_duplicates", {"table": "dok1:t1", "column": "Nr"}, documents)
        assert result["beispiele"][0]["wert"] == "10000"

    def test_searching_for_nan_points_at_the_right_tool(self):
        """Confirmed live: asked 'how many rows have no barcode', a model
        searched the text 'nan', got 0 because an empty cell is missing
        data rather than that word, and concluded every row had one --
        while two genuinely did not."""
        with pytest.raises(ValueError) as excinfo:
            run_tool("count_matching_rows", {
                "table": "dok1:t1", "column": "Barcode", "contains": "nan",
            }, self._catalogue_with_repeated_barcodes())
        assert "empty" in str(excinfo.value)

    def test_empty_filter_finds_what_the_text_search_could_not(self):
        result = run_tool("query_table", {
            "table": "dok1:t1",
            "filters": [{"column": "Barcode", "op": "empty"}],
            "aggregate": {"func": "count"},
        }, self._catalogue_with_repeated_barcodes())
        assert result["anzahl"] == 1
