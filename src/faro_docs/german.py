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
    # A single stray numeric-looking cell in an otherwise textual column
    # (e.g. one part number inside a Bezeichnung/Artikel column) must not
    # convert the whole column, silently turning every real description
    # into NaN. Require a majority of the non-empty values to actually
    # look numeric before assigning any numeric style at all.
    if recognised == 0 or recognised * 2 < len(cleaned):
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


import unicodedata

_UMLAUT_MAP = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"}
)


def decode_text(raw: bytes) -> tuple[str, str]:
    """Decode uploaded bytes to text, repairing German mojibake.

    German CSV exports are frequently cp1252, and text that has already been
    decoded wrongly upstream arrives as StraÃŸe rather than Straße. ftfy fixes
    the second case; it is bundled with Open WebUI so it costs no dependency.
    """
    text = None
    encoding = "utf-8"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            from charset_normalizer import from_bytes

            best = from_bytes(raw).best()
            if best is not None:
                text = str(best)
                encoding = best.encoding
        except Exception:
            text = None
    if text is None:
        try:
            text = raw.decode("cp1252")
            encoding = "cp1252"
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            encoding = "utf-8 (mit Ersatzzeichen)"

    try:
        from ftfy import fix_text

        text = fix_text(text)
    except Exception:
        pass
    return text, encoding


def detect_delimiter(text: str) -> str:
    """Pick the delimiter that splits rows most consistently.

    csv.Sniffer is unreliable here: German files start with invoice preamble
    lines and contain decimal commas, both of which mislead it.
    """
    lines = [line for line in text.splitlines() if line.strip()][:30]
    if not lines:
        return ";"

    best_delimiter = ";"
    best_score = (0, 0.0)
    for candidate in (";", "\t", "|", ","):
        counts = [line.count(candidate) for line in lines]
        populated = [c for c in counts if c > 0]
        if len(populated) < 2:
            continue
        most_common = max(set(populated), key=populated.count)
        consistency = populated.count(most_common) / len(populated)
        score = (most_common, consistency)
        if score > best_score:
            best_score = score
            best_delimiter = candidate
    return best_delimiter


def fold(value: str) -> str:
    """Case- and diacritic-insensitive key for matching German words."""
    text = str(value).strip().translate(_UMLAUT_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "".join(ch for ch in text.lower() if ch.isalnum())
