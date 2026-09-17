from dataclasses import dataclass

import pandas as pd


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


def _load_raw(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        return pd.read_csv(path, header=None)
    return pd.read_excel(path, header=None)


def _compute_facts(df: pd.DataFrame) -> dict:
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

    facts = _compute_facts(df)
    markdown_table = df.to_markdown(index=False)

    return TabularExtraction(markdown_table=markdown_table, facts=facts, dataframe=df)
