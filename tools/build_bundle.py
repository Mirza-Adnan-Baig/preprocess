"""Flatten the core library and adapter into one file for Open WebUI.

Open WebUI Functions are single self-contained files that cannot import local
packages. Hand-maintaining that copy is how the previous version drifted from
src/, so it is generated instead and a test asserts it is current.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLE_PATH = ROOT / "openwebui" / "faro_document_assistant.py"

MODULE_ORDER = [
    "src/faro_docs/model.py",
    "src/faro_docs/german.py",
    "src/faro_docs/tables.py",
    "src/faro_docs/ingest/tabular.py",
    "src/faro_docs/ingest/pdf_ingest.py",
    "src/faro_docs/ingest/router.py",
    "src/faro_docs/facts.py",
    "src/faro_docs/messages_de.py",
    "src/faro_docs/answer.py",
    "adapters/openwebui_pipe.py",
]

ALLOWED_IMPORTS = frozenset({
    # stdlib
    "csv", "glob", "io", "json", "os", "queue", "re", "threading", "time",
    "unicodedata", "dataclasses", "collections", "typing", "email", "pathlib",
    # already present in Open WebUI's environment
    "pandas", "numpy", "openpyxl", "xlrd", "fitz", "PIL", "docx", "pptx",
    "bs4", "lxml", "chardet", "charset_normalizer", "ftfy", "tabulate",
    "ollama", "pydantic", "open_webui",
})

HEADER = '''"""
title: FARO Dokument-Assistent
author: Mirza
version: {version}

GENERIERTE DATEI — NICHT VON HAND BEARBEITEN.
Erzeugt aus src/faro_docs/ durch `python -m tools.build_bundle`.
Änderungen bitte dort vornehmen und neu erzeugen.

Beantwortet Fragen zu hochgeladenen Dokumenten (PDF, Excel, CSV, Text).
Zahlen werden im Code berechnet, nicht vom Sprachmodell geschätzt.
Deutsche Zahlenformate (1.234,56), mehrere Tabellenblätter, Summenzeilen und
mehrere gleichzeitig hochgeladene Dateien werden korrekt behandelt.

WICHTIG vor dem ersten Einsatz: Für dieses Modell muss die eingebaute
Dateiverarbeitung von Open WebUI abgeschaltet werden
(Fähigkeit `file_context` = false), sonst ersetzt Open WebUI die Frage des
Benutzers durch einen eigenen Text. `python -m tools.setup_openwebui` erledigt
das. Ist sie aktiv, warnt diese Funktion im Chat selbst davor.

Benötigt keine zusätzlichen Pakete.
"""
'''

VERSION = "1.0.0"

_INTERNAL_IMPORT = re.compile(r"^\s*(from\s+(src\.faro_docs|adapters)[\w.]*\s+import|import\s+(src\.faro_docs|adapters))")
_IMPORT_LINE = re.compile(r"^(import\s+\S+|from\s+\S+\s+import\s+.+)$")


def _block_end(lines: list[str], start: int) -> int:
    """Index one past the last line of the statement starting at `start`.

    Handles multi-line, parenthesized `from ... import (...)` statements by
    tracking paren depth, so a whole such statement is treated atomically
    instead of leaving its continuation lines (and closing paren) behind.
    """
    depth = lines[start].count("(") - lines[start].count(")")
    end = start + 1
    while depth > 0 and end < len(lines):
        depth += lines[end].count("(") - lines[end].count(")")
        end += 1
    return end


def _split_module(text: str) -> tuple[list[str], list[str]]:
    """Return (top-level import lines, body lines) with internal imports removed."""
    imports, body = [], []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _INTERNAL_IMPORT.match(line):
            i = _block_end(lines, i)
            continue
        if _IMPORT_LINE.match(line) and not line.startswith((" ", "\t")):
            end = _block_end(lines, i)
            imports.append("\n".join(lines[i:end]))
            i = end
            continue
        body.append(line)
        i += 1
    return imports, body


def build() -> str:
    seen_imports: list[str] = []
    sections: list[str] = []

    for relative in MODULE_ORDER:
        text = (ROOT / relative).read_text(encoding="utf-8")
        imports, body = _split_module(text)
        for line in imports:
            if line not in seen_imports:
                seen_imports.append(line)
        sections.append(f"# ---- {relative} ----\n" + "\n".join(body).strip() + "\n")

    return (
        HEADER.format(version=VERSION)
        + "\n"
        + "\n".join(sorted(set(seen_imports)))
        + "\n\n\n"
        + "\n\n".join(sections)
    )


def main() -> None:
    BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE_PATH.write_text(build(), encoding="utf-8")
    print(f"Geschrieben: {BUNDLE_PATH} ({len(build())} Zeichen)")


if __name__ == "__main__":
    main()
