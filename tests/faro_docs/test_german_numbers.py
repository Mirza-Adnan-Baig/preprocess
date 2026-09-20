import pandas as pd
import pytest

from src.faro_docs.german import (
    detect_numeric_format,
    document_numeric_fallback,
    parse_number,
    to_numeric_series,
)


class TestDetection:
    def test_decimal_comma_is_german(self):
        style, rule, confident = detect_numeric_format(["12,00", "0,5", "149,82"])
        assert style == "german"
        assert confident
        assert rule  # German explanation present

    def test_thousands_dot_with_decimal_comma_is_german(self):
        style, _, confident = detect_numeric_format(["1.234,56", "999,00"])
        assert style == "german"
        assert confident

    def test_thousands_comma_with_decimal_point_is_english(self):
        style, _, confident = detect_numeric_format(["1,234.56", "999.00"])
        assert style == "english"
        assert confident

    def test_two_decimal_places_after_dot_is_english(self):
        style, _, _ = detect_numeric_format(["12.00", "149.82"])
        assert style == "english"

    def test_bare_thousands_dot_defaults_to_german(self):
        # The proven bug: these are 1234 / 2500, not 1.234 / 2.5
        style, _, confident = detect_numeric_format(["1.234", "2.500", "750"])
        assert style == "german"
        assert confident is False  # ambiguous, decision must be recorded

    def test_bare_thousands_dot_reads_english_when_document_says_so(self):
        style, _, _ = detect_numeric_format(["1.234", "2.500"], fallback_style="english")
        assert style == "english"

    def test_plain_integers_are_integer(self):
        style, _, _ = detect_numeric_format(["1", "42", "750"])
        assert style == "integer"

    def test_leading_zero_digit_strings_are_not_numeric(self):
        """An EAN/barcode, article number, or German postal code that
        happens to start with 0 is an identifier, not a quantity --
        converting it to a number would silently drop the leading zero
        and change the actual value (found on a real products export:
        '0107610691403' becoming 107610691403)."""
        style, rule, confident = detect_numeric_format(
            ["4836810209885", "0107610691403", "6371019679643"]
        )
        assert style == "none"
        assert confident

    def test_a_single_leading_zero_value_disqualifies_the_whole_column(self):
        style, _, _ = detect_numeric_format(["1", "2", "007"])
        assert style == "none"

    def test_the_value_zero_alone_is_not_treated_as_leading_zero(self):
        style, _, _ = detect_numeric_format(["0", "1", "2"])
        assert style == "integer"

    def test_non_numeric_column_is_none(self):
        style, _, _ = detect_numeric_format(["iPhone Display", "USB-C Kabel"])
        assert style == "none"

    def test_empty_column_is_none(self):
        style, _, _ = detect_numeric_format([None, "", "   "])
        assert style == "none"

    def test_mixed_evidence_is_not_confident(self):
        style, _, confident = detect_numeric_format(["1.234,56", "1,234.56"])
        assert style in {"german", "english"}
        assert confident is False

    def test_currency_and_whitespace_are_ignored(self):
        style, _, _ = detect_numeric_format(["1.234,56 €", "  EUR 99,00"])
        assert style == "german"


class TestParsing:
    @pytest.mark.parametrize(
        "text,style,expected",
        [
            ("1.234,56", "german", 1234.56),
            ("12,00", "german", 12.0),
            ("0,5", "german", 0.5),
            ("1.234", "german", 1234.0),
            ("1 234,56", "german", 1234.56),
            ("1 234,56", "german", 1234.56),
            ("1,234.56", "english", 1234.56),
            ("1.234", "english", 1.234),
            ("750", "integer", 750.0),
            ("1.234,56 €", "german", 1234.56),
            ("-1.234,56", "german", -1234.56),
            ("1.234,56-", "german", -1234.56),
            ("(1.234,56)", "german", -1234.56),
        ],
    )
    def test_parses_value(self, text, style, expected):
        assert parse_number(text, style) == pytest.approx(expected)

    def test_unparseable_returns_none(self):
        assert parse_number("keine Zahl", "german") is None
        assert parse_number(None, "german") is None

    def test_passes_through_real_numbers(self):
        assert parse_number(42, "german") == 42.0


class TestSeries:
    def test_german_series_sums_correctly(self):
        # The regression that motivated this whole task
        series = pd.Series(["1.234", "2.500", "750"])
        assert to_numeric_series(series, "german").sum() == pytest.approx(4484.0)

    def test_german_decimals_are_not_dropped(self):
        series = pd.Series(["12,00", "8,50"])
        result = to_numeric_series(series, "german")
        assert result.notna().all()
        assert result.sum() == pytest.approx(20.5)


class TestDocumentFallback:
    def test_english_evidence_anywhere_sets_english_fallback(self):
        assert document_numeric_fallback([["1,234.56"], ["1.234"]]) == "english"

    def test_german_is_the_default(self):
        assert document_numeric_fallback([["Artikel"], ["1.234"]]) == "german"
