import shutil

import pytest

from src.agent import answer_question
from src.extractors.router import extract_document
from scripts.generate_synthetic_data import generate_invoice_pdf

OLLAMA_MODEL = "qwen2.5:7b"

requires_ollama = pytest.mark.skipif(
    shutil.which("ollama") is None,
    reason="ollama not installed/available on this machine",
)


@requires_ollama
def test_agent_counts_line_items_exactly(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=17, seed=9)

    extraction = extract_document(str(path))
    answer = answer_question(OLLAMA_MODEL, extraction, "How many line items are on this invoice? Answer with just the number.")

    assert "17" in answer
