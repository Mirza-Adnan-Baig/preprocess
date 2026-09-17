import pandas as pd
from src.extractors.tabular import extract_tabular
from scripts.generate_synthetic_data import generate_messy_inventory_xlsx


def test_extract_tabular_skips_title_and_blank_rows(tmp_path):
    path = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(path), n_rows=40, seed=2)

    result = extract_tabular(str(path))

    assert list(result.dataframe.columns) == ["Article", "Quantity", "Unit Price EUR", "Supplier"]
    assert result.facts["row_count"] == 40


def test_extract_tabular_facts_are_exact(tmp_path):
    path = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(path), n_rows=40, seed=2)

    result = extract_tabular(str(path))
    expected_sum = pd.to_numeric(result.dataframe["Quantity"], errors="coerce").sum()

    assert result.facts["columns"]["Quantity"]["sum"] == expected_sum
    assert result.facts["columns"]["Quantity"]["unique_count"] == result.dataframe["Quantity"].nunique()


def test_extract_tabular_markdown_contains_header(tmp_path):
    path = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(path), n_rows=5, seed=3)

    result = extract_tabular(str(path))

    assert "Article" in result.markdown_table
    assert "FARO Inventory Export" not in result.markdown_table
