import pandas as pd


def normalize_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert columns to numeric dtype where every non-null value converts cleanly.

    Extractors that read raw text (CSV via csv.reader, PDF tables via PyMuPDF)
    produce DataFrames where every cell is a Python string, even for numeric
    columns. That breaks tool-calling filters like `Quantity > 10`, which need
    a real numeric dtype to compare against.

    A column is only converted when *all* of its non-null values parse as
    numbers; a single non-numeric value leaves the column untouched, since
    that's a genuine text column (or intentionally mixed data) that shouldn't
    be silently coerced to NaN.
    """
    df = df.copy()
    for col in df.columns:
        original_non_null = df[col].notna().sum()
        if original_non_null == 0:
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().sum() == original_non_null:
            df[col] = converted
    return df
