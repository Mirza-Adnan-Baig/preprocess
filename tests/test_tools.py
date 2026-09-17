import pandas as pd
import pytest
from src.tools import count_rows, sum_column, get_row


@pytest.fixture
def df():
    return pd.DataFrame({
        "Article": ["Screen", "Battery", "Case", "Screen"],
        "Quantity": [10, 5, 20, 3],
        "Unit Price EUR": [50.0, 20.0, 5.0, 50.0],
    })


def test_count_rows_no_filter(df):
    assert count_rows(df) == 4


def test_count_rows_with_filter(df):
    assert count_rows(df, "`Unit Price EUR` > 10") == 3


def test_sum_column_no_filter(df):
    assert sum_column(df, "Quantity") == 38


def test_sum_column_with_filter(df):
    assert sum_column(df, "Quantity", "Article == 'Screen'") == 13


def test_get_row(df):
    row = get_row(df, 1)
    assert row["Article"] == "Battery"
    assert row["Quantity"] == 5
