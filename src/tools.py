import pandas as pd


def _apply_filter(df: pd.DataFrame, filter_expr: str | None) -> pd.DataFrame:
    if not filter_expr:
        return df
    return df.query(filter_expr)


def count_rows(df: pd.DataFrame, filter_expr: str | None = None) -> int:
    return len(_apply_filter(df, filter_expr))


def sum_column(df: pd.DataFrame, column: str, filter_expr: str | None = None) -> float:
    subset = _apply_filter(df, filter_expr)
    return float(pd.to_numeric(subset[column], errors="coerce").sum())


def get_row(df: pd.DataFrame, index: int) -> dict:
    return df.iloc[index].to_dict()


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "count_rows",
            "description": "Count rows in the uploaded table. Optionally filter first using a pandas query expression (column names with spaces need backticks, e.g. `Unit Price EUR` > 10).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_expr": {"type": "string", "description": "Optional pandas query expression"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sum_column",
            "description": "Sum a numeric column in the uploaded table, optionally filtered by a pandas query expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "column": {"type": "string", "description": "Exact column name to sum"},
                    "filter_expr": {"type": "string", "description": "Optional pandas query expression"},
                },
                "required": ["column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_row",
            "description": "Get a single row from the uploaded table by its zero-based index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Zero-based row index"}
                },
                "required": ["index"],
            },
        },
    },
]
