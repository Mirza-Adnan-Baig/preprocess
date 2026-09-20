"""German-first parsing: numbers, encodings, delimiters.

Every user and every document is German. German writes 1.234,56 where English
writes 1,234.56, which means a naive pd.to_numeric turns 1.234 into the float
1.234 and throws away 12,00 as "not a number". Both failures are silent and
produce confidently wrong totals, so format is decided once per column from all
its values -- never per cell -- and the decision is recorded so any number can
be explained afterwards.
"""

import re
from collections.abc import Iterable

import pandas as pd

_SPACES = "    "
_CURRENCY = re.compile(r"(?i)(?:€|eur|chf)")

# Exactly three digits per group is what makes a dot a thousands separator.
_GERMAN_GROUPED_DECIMAL = re.compile(rf"^\d{{1,3}}(?:[.{_SPACES}]\d{{3}})+,\d+$")
_GERMAN_DECIMAL = re.compile(r"^\d+,\d+$")
_ENGLISH_GROUPED_DECIMAL = re.compile(r"^\d{1,3}(?:,\d{3})+\.\d+$")
_ENGLISH_DECIMAL = re.compile(r"^\d+\.\d{1,2}$|^\d+\.\d{4,}$")
_AMBIGUOUS_DOTTED = re.compile(rf"^\d{{1,3}}(?:[.{_SPACES}]\d{{3}})+$")
_ENGLISH_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_PLAIN_INTEGER = re.compile(r"^\d+$")

RULE_DECIMAL_COMMA = "Dezimalkomma erkannt (deutsches Format)"
RULE_DECIMAL_POINT = "Dezimalpunkt erkannt (englisches Format)"
RULE_AMBIGUOUS_GERMAN = (
    "Punkt als Tausendertrennzeichen gedeutet (deutsches Format); "
    "eindeutig ist es nicht"
)
RULE_AMBIGUOUS_ENGLISH = (
    "Punkt als Dezimaltrennzeichen gedeutet (englisches Format); "
    "eindeutig ist es nicht"
)
RULE_MIXED = "Gemischte Zahlenformate in derselben Spalte"
RULE_INTEGER = "Ganze Zahlen ohne Trennzeichen"
RULE_NONE = "Keine Zahlenspalte"


def _clean(value: object) -> str:
    """Strip currency, whitespace and negative notation, keeping digits and separators."""
    if value is None or isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    text = _CURRENCY.sub("", text).strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    text = text.lstrip("+-").rstrip("-").strip()
    return text


def _is_negative(value: object) -> bool:
    text = str(value).strip()
    return text.startswith("-") or text.endswith("-") or (
        text.startswith("(") and text.endswith(")")
    )


def detect_numeric_format(
    values: Iterable[object], fallback_style: str = "german"
) -> tuple[str, str, bool]:
    """Decide one numeric format for a whole column.

    Returns (style, german_rule_text, confident). Style is one of
    "german", "english", "integer", "none".
    """
    cleaned = [_clean(v) for v in values]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return "none", RULE_NONE, True

    german = sum(
        bool(_GERMAN_GROUPED_DECIMAL.match(c) or _GERMAN_DECIMAL.match(c))
        for c in cleaned
    )
    english = sum(
        bool(_ENGLISH_GROUPED_DECIMAL.match(c) or _ENGLISH_DECIMAL.match(c))
        for c in cleaned
    )
    ambiguous = sum(bool(_AMBIGUOUS_DOTTED.match(c)) for c in cleaned)
    english_grouped = sum(bool(_ENGLISH_GROUPED.match(c)) for c in cleaned)
    plain = sum(bool(_PLAIN_INTEGER.match(c)) for c in cleaned)

    recognised = german + english + ambiguous + english_grouped + plain
    if recognised == 0:
        return "none", RULE_NONE, True

    if german and english:
        winner = "german" if german >= english else "english"
        return winner, RULE_MIXED, False
    if german:
        return "german", RULE_DECIMAL_COMMA, True
    if english:
        return "english", RULE_DECIMAL_POINT, True
    if english_grouped:
        return "english", RULE_DECIMAL_POINT, True
    if ambiguous:
        # 1.234 with nothing else to go on. Spec §7 resolution order (c):
        # default German unless the document says otherwise.
        if fallback_style == "english":
            return "english", RULE_AMBIGUOUS_ENGLISH, False
        return "german", RULE_AMBIGUOUS_GERMAN, False
    return "integer", RULE_INTEGER, True


def document_numeric_fallback(columns: Iterable[Iterable[object]]) -> str:
    """Scan a whole document for unambiguous English formatting.

    Used to resolve columns that are ambiguous on their own (spec §7 rule b).
    """
    for column in columns:
        for value in column:
            cleaned = _clean(value)
            if cleaned and (
                _ENGLISH_GROUPED_DECIMAL.match(cleaned) or _ENGLISH_DECIMAL.match(cleaned)
            ):
                return "english"
    return "german"


def parse_number(value: object, style: str) -> float | None:
    """Parse one value using an already-decided column style."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if pd.isna(value):
            return None
        return float(value)

    cleaned = _clean(value)
    if not cleaned:
        return None

    negative = _is_negative(value)

    if style == "german":
        normalised = cleaned
        for space in _SPACES:
            normalised = normalised.replace(space, "")
        normalised = normalised.replace(".", "").replace(",", ".")
    elif style == "english":
        normalised = cleaned.replace(",", "")
    elif style == "integer":
        normalised = cleaned
        for space in _SPACES:
            normalised = normalised.replace(space, "")
        normalised = normalised.replace(".", "").replace(",", "")
    else:
        return None

    try:
        result = float(normalised)
    except ValueError:
        return None
    return -result if negative else result


def to_numeric_series(series: pd.Series, style: str) -> pd.Series:
    """Convert a column to real numbers using its decided style."""
    if style == "none":
        return pd.Series([None] * len(series), index=series.index, dtype="float64")
    return pd.Series(
        [parse_number(v, style) for v in series], index=series.index, dtype="float64"
    )
