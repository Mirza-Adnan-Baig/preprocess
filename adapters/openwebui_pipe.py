"""Open WebUI Pipe adapter. Contains no document logic -- that lives in faro_docs."""

import glob
import os
import re

from pydantic import BaseModel

from src.faro_docs.answer import answer
from src.faro_docs.ingest.router import ingest_all
from src.faro_docs.messages_de import (
    EXTRAHIERE,
    EXTRAHIERE_EN,
    KEINE_DATEI,
    KEINE_DATEI_EN,
    KEINE_TABELLE,
    KEINE_TABELLE_EN,
    RAG_WARNUNG,
    RAG_WARNUNG_EN,
)

_RAG_MARKERS = ("### Task:", "inline citations", "<source")
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)


def looks_like_openwebui_rag(message: str) -> bool:
    """Open WebUI rewrites the user's question when file_context is enabled.

    An invisible failure otherwise: the pipe answers a prompt the user never
    wrote, confidently and wrongly.
    """
    return sum(marker in message for marker in _RAG_MARKERS) >= 2


def recover_question(message: str) -> str:
    """Pull the real question back out of Open WebUI's template."""
    match = _USER_QUERY.search(message)
    if match:
        return match.group(1).strip()
    return message


def _read_file(info: dict) -> bytes | None:
    path = info.get("path")
    if path and os.path.isfile(path):
        with open(path, "rb") as handle:
            return handle.read()

    file_id = info.get("id", "")
    directories = [
        os.environ.get("UPLOAD_DIR", ""),
        os.environ.get("DATA_DIR", ""),
        "/app/backend/data/uploads",
        "./data/uploads",
    ]
    try:
        import open_webui

        directories.append(
            os.path.join(os.path.dirname(open_webui.__file__), "data", "uploads")
        )
    except ImportError:
        pass

    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        matches = glob.glob(os.path.join(directory, f"*{file_id}*"))
        if matches:
            with open(matches[0], "rb") as handle:
                return handle.read()
    return None


def collect_files(files: list) -> list[tuple[str, bytes]]:
    """Every attached file, not just the first."""
    collected = []
    for entry in files or []:
        info = entry.get("file", {})
        raw = _read_file(info)
        if raw is not None:
            collected.append((info.get("filename", "unbenannt"), raw))
    return collected


class Pipe:
    class Valves(BaseModel):
        MODEL: str = "qwen3.6:27b"
        OLLAMA_HOST: str = ""
        MAX_TEXT_CHARS: int = 40000
        RESPONSE_LANGUAGE: str = ""

    def __init__(self):
        self.id = "faro_document_assistant"
        self.name = "FARO Dokument-Assistent"
        self.valves = self.Valves()

    def _host(self) -> str:
        if self.valves.OLLAMA_HOST:
            return self.valves.OLLAMA_HOST
        return (
            os.environ.get("OLLAMA_BASE_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://localhost:11434"
        )

    def pipe(self, body: dict, __files__: list = None, __user__: dict = None):
        message = body.get("messages", [{}])[-1].get("content", "")
        forced_english = self.valves.RESPONSE_LANGUAGE == "en"

        if not __files__:
            yield KEINE_DATEI_EN if forced_english else KEINE_DATEI
            return

        if looks_like_openwebui_rag(message):
            yield (RAG_WARNUNG_EN if forced_english else RAG_WARNUNG) + "\n"
        question = recover_question(message)

        yield (EXTRAHIERE_EN if forced_english else EXTRAHIERE) + "\n\n"

        files = collect_files(__files__)
        if not files:
            yield KEINE_DATEI_EN if forced_english else KEINE_DATEI
            return

        documents = ingest_all(files)
        if not any(document.tables for document in documents):
            yield (KEINE_TABELLE_EN if forced_english else KEINE_TABELLE) + "\n\n"

        for chunk in answer(
            documents,
            question,
            model=self.valves.MODEL,
            host=self._host(),
            max_text_chars=self.valves.MAX_TEXT_CHARS,
            response_language=self.valves.RESPONSE_LANGUAGE,
        ):
            yield chunk
