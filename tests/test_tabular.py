import csv

import pandas as pd
from src.extractors.tabular import extract_tabular
from src.tools import count_rows
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


def test_extract_tabular_csv_numeric_columns_are_numeric_dtype(tmp_path):
    """CSV-derived numeric columns must be real numeric dtype so tool filters
    like `Quantity > 10` don't raise TypeError comparing str to int."""
    path = tmp_path / "inventory.csv"
    with open(str(path), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Article", "Quantity", "Unit Price EUR", "Supplier"])
        writer.writerow(["Widget A", "100", "12.50", "SupplierX"])
        writer.writerow(["Widget B", "5", "25.00", "SupplierY"])
        writer.writerow(["Widget C", "75", "18.00", "SupplierZ"])

    result = extract_tabular(str(path))

    assert pd.api.types.is_numeric_dtype(result.dataframe["Quantity"])
    # this used to raise TypeError: '>' not supported between 'str' and 'int'
    assert count_rows(result.dataframe, "Quantity > 10") == 2


def test_extract_tabular_csv_text_columns_stay_text(tmp_path):
    """A genuinely non-numeric column (like Article names) must not be coerced."""
    path = tmp_path / "inventory.csv"
    with open(str(path), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Article", "Quantity", "Unit Price EUR", "Supplier"])
        writer.writerow(["Widget A", "100", "12.50", "SupplierX"])
        writer.writerow(["Widget B", "5", "25.00", "SupplierY"])

    result = extract_tabular(str(path))

    assert not pd.api.types.is_numeric_dtype(result.dataframe["Article"])
    assert list(result.dataframe["Article"]) == ["Widget A", "Widget B"]


def test_extract_tabular_german_csv_semicolon_cp1252(tmp_path):
    """German Excel CSV exports commonly use ';' delimiters and cp1252 encoding."""
    path = tmp_path / "inventory_de.csv"
    rows = [
        ["Artikel", "Menge", "Preis EUR", "Lieferant"],
        ["Schraube groß", "100", "12.50", "Lieferant A"],
        ["Mutter klein", "50", "3.00", "Lieferant B"],
        ["Schräubchen", "25", "1.00", "Lieferant C"],
    ]
    with open(str(path), "w", newline="", encoding="cp1252") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerows(rows)

    result = extract_tabular(str(path))

    assert list(result.dataframe.columns) == ["Artikel", "Menge", "Preis EUR", "Lieferant"]
    assert result.facts["row_count"] == 3
    assert "Schraube groß" in list(result.dataframe["Artikel"])
    assert "Schräubchen" in list(result.dataframe["Artikel"])
