# local-llm-pipeline

Prototype document-preprocessing pipeline for the FARO local-LLM project.
Runs entirely on local hardware with synthetic test data — no dependency on
the Mac Studio or real company documents. See
`docs/superpowers/specs/2026-09-18-local-llm-document-preprocessing-design.md`
in the parent repo for the full design.

## Setup

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

Install the Tesseract OCR binary separately (required for scanned-PDF
fallback): https://github.com/tesseract-ocr/tesseract

## Test

    pytest -v
