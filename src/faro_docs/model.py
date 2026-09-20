from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ColumnInfo:
    """How one column was interpreted, so any answer derived from it can be explained."""

    name: str
    numeric_style: str = "none"  # "german" | "english" | "integer" | "none"
    numeric_rule: str = ""  # German explanation of why this style was chosen
    numeric_confident: bool = True


@dataclass
class Table:
    id: str
    label: str
    frame: pd.DataFrame
    columns: dict[str, ColumnInfo] = field(default_factory=dict)
    totals_rows: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def row_count(self) -> int:
        return len(self.frame)


@dataclass
class Document:
    id: str
    filename: str
    media_type: str
    text: str = ""
    tables: list[Table] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def total_rows(self) -> int:
        return sum(table.row_count() for table in self.tables)
