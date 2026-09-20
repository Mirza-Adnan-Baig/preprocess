"""Every string a user can see. German only -- the users are non-technical Germans."""

KEINE_DATEI = "Bitte hängen Sie eine Datei an Ihre Frage an (PDF, Excel, CSV oder Text)."
EXTRAHIERE = "_(Dokument wird ausgewertet …)_"
DENKT_NACH = "_(arbeitet noch, {sekunden} s …)_"
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

_QUELLEN = {
    "tabelle": "aus der Tabelle berechnet",
    "text": "aus dem Dokumenttext gelesen",
    "ocr": "per Texterkennung gelesen (unsicher)",
}


def provenance_footer(sources: set[str], notes: list[str]) -> str:
    """One short German line saying where the answer came from."""
    parts = [_QUELLEN[s] for s in ("tabelle", "text", "ocr") if s in sources]
    if not parts and not notes:
        return ""
    lines = []
    if parts:
        lines.append("_Herkunft: " + ", ".join(parts) + "._")
    lines.extend(f"_{note}_" for note in notes)
    return "\n\n" + "\n".join(lines)
