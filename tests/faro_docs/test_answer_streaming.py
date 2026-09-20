"""End-to-end streaming behaviour of answer(), against a fake Ollama client.

Covers the heartbeat UX: Ollama's prefill on a large document can take long
enough that several heartbeat ticks fire before the first real token -- the
visible "still working" notice must appear at most once per wait, not once
per tick, with any further ticks reduced to an invisible keep-alive so the
connection still sees regular bytes.
"""

import time

import pandas as pd
import pytest

from src.faro_docs.answer import DENKT_NACH, DENKT_NACH_EN, _KEEPALIVE, answer
from src.faro_docs.model import ColumnInfo, Document, Table


def _document():
    frame = pd.DataFrame({"Artikel": ["A", "B"], "Menge": [1.0, 2.0]})
    return [
        Document(
            id="dok1", filename="a.csv", media_type="text/csv", text="FARO GmbH",
            tables=[Table(id="dok1:t1", label="Tabelle 1", frame=frame,
                          columns={"Menge": ColumnInfo("Menge", "german", "", True)})],
        )
    ]


class _FakeClient:
    """Mimics ollama.Client: .chat(..., stream=True) returns an iterator of
    chunks, after an initial delay long enough to trigger several heartbeat
    ticks at the test's tiny interval."""

    def __init__(self, delay: float, chunks: list[dict]):
        self.delay = delay
        self.chunks = chunks

    def chat(self, model, messages, tools, stream, options=None):
        self.last_options = options
        time.sleep(self.delay)
        yield from self.chunks


def _install_fake_client(monkeypatch, client):
    import ollama

    monkeypatch.setattr(ollama, "Client", lambda host: client)


def test_heartbeat_notice_shown_once_not_once_per_tick(monkeypatch):
    """A slow prefill (several heartbeat ticks) must not stack up several
    visible '(still working ...)' lines -- exactly one, ever, per wait."""
    client = _FakeClient(
        delay=0.25,
        chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    chunks = list(
        answer(
            _document(), "Was steht im Dokument?", model="m", host="h",
            heartbeat_interval=0.05,
        )
    )
    text = "".join(chunks)

    assert text.count(DENKT_NACH) == 1
    assert DENKT_NACH_EN not in text
    assert "Antwort." in text


def test_extra_heartbeat_ticks_are_invisible_keepalive(monkeypatch):
    """Ticks after the first notice must still put bytes on the wire (so the
    connection doesn't look idle) but render as nothing visible."""
    client = _FakeClient(
        delay=0.3,
        chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    chunks = list(
        answer(
            _document(), "Was steht im Dokument?", model="m", host="h",
            heartbeat_interval=0.05,
        )
    )

    assert any(chunk == _KEEPALIVE for chunk in chunks)
    assert _KEEPALIVE == "​"  # zero-width space: real bytes, renders as nothing


def test_no_heartbeat_when_the_model_answers_immediately(monkeypatch):
    """A fast answer (no wait longer than one interval) never shows the
    notice at all -- it exists only to cover genuinely long prefills."""
    client = _FakeClient(
        delay=0.0,
        chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    chunks = list(
        answer(
            _document(), "Was steht im Dokument?", model="m", host="h",
            heartbeat_interval=1.0,
        )
    )
    text = "".join(chunks)

    assert DENKT_NACH not in text


def test_num_ctx_is_always_passed_to_ollama(monkeypatch):
    """Ollama silently defaults to a 4096-token window regardless of what the
    model supports (confirmed via `ollama ps` against a real local model) --
    a real multi-hundred-row table plus its FAKTEN block can exceed that,
    and Ollama quietly truncates the *oldest* part of the prompt to fit
    rather than erroring, so the model confidently answers from a table it
    never fully saw. num_ctx must be set explicitly on every call, not left
    to Ollama's default."""
    client = _FakeClient(
        delay=0.0, chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    list(answer(_document(), "Frage?", model="m", host="h", num_ctx=16384))

    assert client.last_options == {"num_ctx": 16384}


def test_response_language_en_uses_english_notice(monkeypatch):
    client = _FakeClient(
        delay=0.25,
        chunks=[{"message": {"content": "Answer.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    chunks = list(
        answer(
            _document(), "What is in the document?", model="m", host="h",
            response_language="en", heartbeat_interval=0.05,
        )
    )
    text = "".join(chunks)

    assert text.count(DENKT_NACH_EN) == 1
    assert DENKT_NACH not in text
