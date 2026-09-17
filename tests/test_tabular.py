import csv

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


def test_extract_tabular_ragged_csv(tmp_path):
    """Test that ragged CSV (short title row, wider data rows) is handled correctly."""
    path = tmp_path / "inventory.csv"

    # Create a ragged CSV inline: title row (1 cell), blank row, header row (4 cells), data rows (4 cells)
    with open(str(path), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["FARO Inventory Export"])  # Title row: 1 column
        writer.writerow([])  # Blank row
        writer.writerow(["Article", "Quantity", "Unit Price EUR", "Supplier"])  # Header: 4 columns
        writer.writerow(["Widget A", "100", "12.50", "SupplierX"])  # Data rows
        writer.writerow(["Widget B", "50", "25.00", "SupplierY"])
        writer.writerow(["Widget C", "75", "18.00", "SupplierZ"])

    # Should not raise ParserError on ragged CSV
    result = extract_tabular(str(path))

    # Verify correct header detection
    assert list(result.dataframe.columns) == ["Article", "Quantity", "Unit Price EUR", "Supplier"]
    # Verify correct row count (3 data rows)
    assert result.facts["row_count"] == 3
