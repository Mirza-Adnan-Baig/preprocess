from dataclasses import dataclass

import pandas as pd

from src.extractors.tabular import extract_tabular, compute_facts
from src.extractors.pdf import extract_pdf
from src.extractors.dtypes import normalize_numeric_columns

TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


def _build_pdf_dataframe(result) -> pd.DataFrame | None:
    groups: dict[tuple, list[list]] = {}
    for page in result.pages:
        for rows in page.tables_raw:
            if len(rows) < 2:
                continue  # no header + data
            header = tuple(rows[0])
            groups.setdefault(header, []).extend(rows[1:])
    if not groups:
        return None
    header, body = max(groups.items(), key=lambda kv: len(kv[1]))
    df = pd.DataFrame(body, columns=[str(c) for c in header])
    df = df.dropna(axis=0, how="all").reset_index(drop=True)
    df = normalize_numeric_columns(df)
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
        try:
            result = extract_tabular(path)
        except Exception as e:
            return ExtractionResult(
                kind="tabular",
                markdown=None,
                facts=None,
                dataframe=None,
                parse_failed=True,
                message=f"Couldn't fully parse this file: {e}",
            )
        return ExtractionResult(
            kind="tabular",
            markdown=result.markdown_table,
            facts=result.facts,
            dataframe=result.dataframe,
            parse_failed=False,
            message=None,
        )

    if ext == "pdf":
        try:
            result = extract_pdf(path)
        except Exception as e:
            return ExtractionResult(
                kind="pdf",
                markdown=None,
                facts=None,
                dataframe=None,
                parse_failed=True,
                message=f"Couldn't fully parse this file: {e}",
            )
        table_blocks = "\n\n".join(
            t for page in result.pages for t in page.tables_markdown
        )
        markdown = result.full_text + ("\n\n" + table_blocks if table_blocks else "")
        dataframe = _build_pdf_dataframe(result)
        facts = {"page_count": len(result.pages)}
        if dataframe is not None:
            facts = {**facts, **compute_facts(dataframe)}
        return ExtractionResult(
            kind="pdf",
            markdown=markdown,
            facts=facts,
            dataframe=dataframe,
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
