import json
from urllib.error import HTTPError

import pytest

from tools.setup_openwebui import _normalize_base_url, disable_file_context


class TestNormalizeBaseUrl:
    def test_strips_a_trailing_slash(self):
        """A trailing slash made a real office deployment's sign-in request
        fail with HTTPError 405 (the appended path became a double slash)."""
        assert _normalize_base_url("http://192.168.1.50:3000/") == "http://192.168.1.50:3000"

    def test_strips_multiple_trailing_slashes(self):
        assert _normalize_base_url("http://192.168.1.50:3000///") == "http://192.168.1.50:3000"

    def test_leaves_a_clean_url_unchanged(self):
        assert _normalize_base_url("http://192.168.1.50:3000") == "http://192.168.1.50:3000"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_creates_the_model_override_and_refreshes(monkeypatch):
    calls = []

    def fake_urlopen(request, *args, **kwargs):
        url = request.full_url
        calls.append(url)
        if "/models/create" in url:
            return _FakeResponse({"id": "faro_document_assistant"})
        return _FakeResponse({"data": []})

    monkeypatch.setattr("tools.setup_openwebui.urlopen", fake_urlopen)
    result = disable_file_context("http://x", "tok", "faro_document_assistant")

    assert any("/models/create" in c for c in calls)
    assert any("refresh=true" in c for c in calls), "Modell-Cache muss neu geladen werden"
    assert result["ok"]


def test_falls_back_to_update_when_entry_exists(monkeypatch):
    calls = []

    def fake_urlopen(request, *args, **kwargs):
        url = request.full_url
        calls.append(url)
        if "/models/create" in url:
            raise HTTPError(url, 401, "exists", {}, None)
        return _FakeResponse({"id": "faro_document_assistant"})

    monkeypatch.setattr("tools.setup_openwebui.urlopen", fake_urlopen)
    result = disable_file_context("http://x", "tok", "faro_document_assistant")

    assert any("/model/update" in c for c in calls)
    assert result["ok"]


def test_reraises_non_conflict_http_errors(monkeypatch):
    def fake_urlopen(request, *args, **kwargs):
        if "/models/create" in request.full_url:
            raise HTTPError(request.full_url, 500, "server error", {}, None)
        return _FakeResponse({"data": []})

    monkeypatch.setattr("tools.setup_openwebui.urlopen", fake_urlopen)
    with pytest.raises(HTTPError):
        disable_file_context("http://x", "tok", "faro_document_assistant")
