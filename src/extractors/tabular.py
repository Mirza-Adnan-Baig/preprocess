import csv
import io
from dataclasses import dataclass

import pandas as pd

from src.extractors.dtypes import normalize_numeric_columns


@dataclass
class TabularExtraction:
    markdown_table: str
    facts: dict
    dataframe: pd.DataFrame


def _detect_header_row(raw: pd.DataFrame, max_scan: int = 10) -> int:
    best_row = 0
    best_score = -1
    for i in range(min(max_scan, len(raw))):
        row = raw.iloc[i]
        non_null = row.notna().sum()
        non_numeric_strings = sum(
            isinstance(v, str) and not v.strip().replace(".", "", 1).isdigit()
            for v in row
            if pd.notna(v)
        )
        score = non_null + non_numeric_strings
        if score > best_score:
            best_score = score
            best_row = i
    return best_row


def _read_csv_text(path: str) -> str:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, newline="", encoding="cp1252") as f:
            return f.read()


def _detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _load_raw(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        text = _read_csv_text(path)
        delimiter = _detect_delimiter(text[:4096])
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
        width = max((len(r) for r in rows), default=0)
        padded = [r + [None] * (width - len(r)) for r in rows]
        return pd.DataFrame(padded)
    return pd.read_excel(path, header=None)


def compute_facts(df: pd.DataFrame) -> dict:
    facts = {"row_count": len(df), "columns": {}}
    for col in df.columns:
        numeric = pd.to_numeric(df[col], errors="coerce")
        has_numeric = numeric.notna().any()
        facts["columns"][col] = {
            "sum": float(numeric.sum()) if has_numeric else None,
            "min": float(numeric.min()) if has_numeric else None,
            "max": float(numeric.max()) if has_numeric else None,
            "avg": float(numeric.mean()) if has_numeric else None,
            "unique_count": int(df[col].nunique(dropna=True)),
        }
    return facts


def extract_tabular(path: str) -> TabularExtraction:
    raw = _load_raw(path)
    header_row = _detect_header_row(raw)

    header = raw.iloc[header_row]
    df = raw.iloc[header_row + 1:].copy()
    df.columns = [str(c) for c in header]
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    df = df.reset_index(drop=True)
    df = normalize_numeric_columns(df)

    facts = compute_facts(df)
    markdown_table = df.to_markdown(index=False)

    return TabularExtraction(markdown_table=markdown_table, facts=facts, dataframe=df)
