from dataclasses import dataclass

import pandas as pd

from src.extractors.tabular import extract_tabular
from src.extractors.pdf import extract_pdf

TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


@dataclass
class ExtractionResult:
    kind: str
    markdown: str | None
    facts: dict | None
    dataframe: pd.DataFrame | None
    parse_failed: bool
    message: str | None


def extract_document(path: str) -> ExtractionResult:
    ext = path.lower().rsplit(".", 1)[-1]

    if ext in TABULAR_EXTENSIONS:
        result = extract_tabular(path)
        return ExtractionResult(
            kind="tabular",
            markdown=result.markdown_table,
            facts=result.facts,
            dataframe=result.dataframe,
            parse_failed=False,
            message=None,
        )

    if ext == "pdf":
        result = extract_pdf(path)
        table_blocks = "\n\n".join(
            t for page in result.pages for t in page.tables_markdown
        )
        markdown = result.full_text + ("\n\n" + table_blocks if table_blocks else "")
        return ExtractionResult(
            kind="pdf",
            markdown=markdown,
            facts={"page_count": len(result.pages)},
            dataframe=None,
            parse_failed=False,
            message=None,
        )

    return ExtractionResult(
        kind="unknown",
        markdown=None,
        facts=None,
        dataframe=None,
        parse_failed=True,
        message=f"Couldn't fully parse this file (unsupported type: .{ext}).",
    )
