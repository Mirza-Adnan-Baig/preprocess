"""Every fixed string the pipe itself emits.

German by default, since the real users at the office are non-technical
Germans. A RESPONSE_LANGUAGE valve (see adapters/openwebui_pipe.py) can
force everything -- these fixed strings, the note translations below, and
the model's own answer via the system prompt -- into English instead, for
local testing by someone who doesn't read German.
"""

import re

KEINE_DATEI = "Bitte hängen Sie eine Datei an Ihre Frage an (PDF, Excel, CSV oder Text)."
EXTRAHIERE = "_(Dokument wird ausgewertet …)_"
DENKT_NACH = "_(Moment, die Antwort wird vorbereitet …)_"
KEINE_TABELLE = (
    "In dieser Datei wurde keine auswertbare Tabelle gefunden. "
    "Fragen nach genauen Anzahlen oder Summen kann ich deshalb nicht sicher beantworten."
)
OLLAMA_NICHT_ERREICHBAR = (
    "Das Sprachmodell ist unter `{host}` nicht erreichbar. "
    "Bitte prüfen Sie im Admin-Bereich unter Funktionen die Einstellung OLLAMA_HOST."
)
ZU_VIELE_RUNDEN = (
    "Ich konnte innerhalb der erlaubten Schritte keine endgültige Antwort bilden. "
    "Bitte formulieren Sie die Frage etwas genauer."
)
RAG_WARNUNG = (
    "**Achtung: Die eingebaute Dateiverarbeitung von Open WebUI ist aktiv.**\n"
    "Sie ersetzt Ihre Frage durch einen eigenen Text, bevor diese Funktion sie "
    "sieht — die Antworten sind dadurch unzuverlässig.\n"
    "Zu beheben im Admin-Bereich: für dieses Modell die Fähigkeit "
    "`file_context` abschalten.\n"
)

KEINE_DATEI_EN = "Please attach a file to your question (PDF, Excel, CSV, or text)."
EXTRAHIERE_EN = "_(analyzing document …)_"
DENKT_NACH_EN = "_(One moment, preparing the answer …)_"
KEINE_TABELLE_EN = (
    "No usable table was found in this file. "
    "Exact counts or sums can't be answered reliably as a result."
)
OLLAMA_NICHT_ERREICHBAR_EN = (
    "The language model is not reachable at `{host}`. "
    "Please check the OLLAMA_HOST setting under Admin Panel > Functions."
)
ZU_VIELE_RUNDEN_EN = (
    "I couldn't reach a final answer within the allowed steps. "
    "Please phrase the question a bit more precisely."
)
RAG_WARNUNG_EN = (
    "**Warning: Open WebUI's built-in file processing is active.**\n"
    "It replaces your question with its own text before this function ever "
    "sees it, which makes answers unreliable.\n"
    "Fix in the admin area: disable the `file_context` capability for this model.\n"
)

_QUELLEN = {
    "tabelle": "aus der Tabelle berechnet",
    "text": "aus dem Dokumenttext gelesen",
    "ocr": "per Texterkennung gelesen (unsicher)",
}
_QUELLEN_EN = {
    "tabelle": "computed from the table",
    "text": "read from the document text",
    "ocr": "read via text recognition (uncertain)",
}

# The small, fixed vocabulary of German note templates produced deep in
# ingestion (src/faro_docs/german.py, tables.py, ingest/*.py) -- translated
# here, in one place, rather than threading a language parameter through
# every extraction function for a handful of known strings.
_RULE_TRANSLATIONS = {
    "Dezimalkomma erkannt (deutsches Format)": "decimal comma detected (German format)",
    "Dezimalpunkt erkannt (englisches Format)": "decimal point detected (English format)",
    "Punkt als Tausendertrennzeichen gedeutet (deutsches Format); eindeutig ist es nicht": (
        "dot read as a thousands separator (German format); not unambiguous"
    ),
    "Punkt als Dezimaltrennzeichen gedeutet (englisches Format); eindeutig ist es nicht": (
        "dot read as a decimal separator (English format); not unambiguous"
    ),
    "Gemischte Zahlenformate in derselben Spalte": "mixed number formats in the same column",
    "Ganze Zahlen ohne Trennzeichen": "whole numbers with no separators",
    "Keine Zahlenspalte": "not a numeric column",
}

_NOTE_PATTERNS_EN = [
    (
        re.compile(r'^Spalte „(?P<name>.+)“: (?P<rule>.+)\.$'),
        lambda m: f'Column "{m["name"]}": '
        f'{_RULE_TRANSLATIONS.get(m["rule"], m["rule"])}.',
    ),
    (
        re.compile(r"^Datei wurde als (?P<encoding>.+) gelesen\.$"),
        lambda m: f'File was read as {m["encoding"]}.',
    ),
    (
        re.compile(
            r"^Hinweis: (?P<count>\d+) Summenzeile\(n\) wurde\(n\) von Anzahl und "
            r"Summen ausgeschlossen, damit nicht doppelt gezählt wird\.$"
        ),
        lambda m: f'Note: {m["count"]} totals row(s) were excluded from counts '
        "and sums to avoid double-counting.",
    ),
    (
        re.compile(
            r"^Dateityp „(?P<suffix>.+)“ wird nicht unterstützt\. Unterstützt "
            r"werden derzeit PDF, Excel \(XLSX/XLS\), CSV und Textdateien\.$"
        ),
        lambda m: f'File type "{m["suffix"]}" is not supported. Currently '
        "supported: PDF, Excel (XLSX/XLS), CSV, and text files.",
    ),
    (
        re.compile(r"^Die Datei „(?P<filename>.+)“ konnte nicht gelesen werden: (?P<error>.+)$"),
        lambda m: f'The file "{m["filename"]}" could not be read: {m["error"]}',
    ),
    (
        re.compile(
            r"^Diese Datei enthält kaum auslesbaren Text und ist vermutlich ein "
            r"Scan\. Texterkennung ist in dieser Version noch nicht aktiv\.$"
        ),
        lambda m: "This file contains almost no extractable text and is "
        "likely a scan. Text recognition isn't active in this version yet.",
    ),
]


def translate_notes(notes: list[str], language: str) -> list[str]:
    """Translate the fixed-template notes ingestion produces, if language != 'de'.

    Free-form text embedded in a note (a raw exception message, a filename)
    is left as-is -- only the surrounding German template is translated.
    """
    if language == "de":
        return notes
    translated = []
    for note in notes:
        for pattern, render in _NOTE_PATTERNS_EN:
            match = pattern.match(note)
            if match:
                translated.append(render(match))
                break
        else:
            translated.append(note)
    return translated


def provenance_footer(sources: set[str], notes: list[str], language: str = "de") -> str:
    """One short line saying where the answer came from, in the given language."""
    quellen = _QUELLEN if language == "de" else _QUELLEN_EN
    parts = [quellen[s] for s in ("tabelle", "text", "ocr") if s in sources]
    if not parts and not notes:
        return ""
    lines = []
    if parts:
        label = "_Herkunft: " if language == "de" else "_Source: "
        lines.append(label + ", ".join(parts) + "._")
    lines.extend(f"_{note}_" for note in notes)
    return "\n\n" + "\n".join(lines)
