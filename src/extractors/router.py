from dataclasses import dataclass

import pandas as pd

from src.extractors.tabular import extract_tabular
from src.extractors.pdf import extract_pdf

TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


def _build_pdf_dataframe(result) -> pd.DataFrame | None:
    best_rows = None
    for page in result.pages:
        for rows in page.tables_raw:
            if len(rows) < 2:
                continue  # no header + data
            if best_rows is None or len(rows) > len(best_rows):
                best_rows = rows
    if best_rows is None:
        return None
    header, *body = best_rows
    df = pd.DataFrame(body, columns=[str(c) for c in header])
    df = df.dropna(axis=0, how="all").reset_index(drop=True)
    return df


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
            dataframe=_build_pdf_dataframe(result),
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
