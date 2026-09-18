import pandas as pd

from src.agent import _build_messages, NO_TABLE_GUIDANCE
from src.extractors.router import ExtractionResult


def test_build_messages_includes_no_table_guidance_when_dataframe_missing():
    extraction = ExtractionResult(
        kind="pdf",
        markdown="Some invoice text with no detectable table.",
        facts={"page_count": 1},
        dataframe=None,
        parse_failed=False,
        message=None,
    )

    messages = _build_messages(extraction, "How many line items are there?")
    user_content = messages[-1]["content"]

    assert NO_TABLE_GUIDANCE in user_content


def test_build_messages_omits_no_table_guidance_when_dataframe_present():
    extraction = ExtractionResult(
        kind="tabular",
        markdown="| Article | Quantity |\n| --- | --- |\n| Widget | 5 |",
        facts={"row_count": 1},
        dataframe=pd.DataFrame({"Article": ["Widget"], "Quantity": [5]}),
        parse_failed=False,
        message=None,
    )

    messages = _build_messages(extraction, "How many rows?")
    user_content = messages[-1]["content"]

    assert NO_TABLE_GUIDANCE not in user_content
