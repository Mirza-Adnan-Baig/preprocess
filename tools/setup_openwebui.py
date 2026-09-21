"""Turn off Open WebUI's built-in file RAG for this model.

Open WebUI rewrites the user's question whenever a file is attached, unless the
model declares capabilities.file_context = false. The model cache is loaded
lazily, so the change does nothing until /api/models?refresh=true is called --
that second step is not optional.
"""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODEL_ID = "faro_document_assistant"
MODEL_NAME = "FARO Dokument-Assistent"


def _post(url: str, token: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urlopen(request) as response:
        return json.loads(response.read())


def _get(url: str, token: str) -> dict:
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    with urlopen(request) as response:
        return json.loads(response.read())


def _normalize_base_url(url: str) -> str:
    """Strip any trailing slash(es).

    A URL like "http://host:3000/" builds "http://host:3000//api/v1/..."
    (double slash) once "/api/v1/..." is appended -- confirmed at a real
    office deployment to make the very first request (sign-in) fail with
    HTTPError 405 instead of a clearer error, since the server sees a
    different path than intended.
    """
    return url.rstrip("/")


def sign_in(base_url: str, email: str, password: str) -> str:
    result = _post(
        f"{base_url}/api/v1/auths/signin", "", {"email": email, "password": password}
    )
    return result["token"]


def disable_file_context(base_url: str, token: str, model_id: str = MODEL_ID) -> dict:
    payload = {
        "id": model_id,
        "name": MODEL_NAME,
        "meta": {
            "description": "Deterministische Dokumentauswertung; eingebaute Datei-RAG abgeschaltet.",
            "capabilities": {"file_context": False},
        },
        "params": {},
        "is_active": True,
    }
    try:
        _post(f"{base_url}/api/v1/models/create", token, payload)
        action = "erstellt"
    except HTTPError as error:
        if error.code != 401:
            raise
        _post(f"{base_url}/api/v1/models/model/update", token, payload)
        action = "aktualisiert"

    _get(f"{base_url}/api/models?refresh=true", token)
    return {"ok": True, "aktion": action, "model_id": model_id}


def main() -> None:
    parser = argparse.ArgumentParser(description="Open WebUI für den FARO-Assistenten einrichten")
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--model-id", default=MODEL_ID)
    args = parser.parse_args()
    url = _normalize_base_url(args.url)

    token = sign_in(url, args.email, args.password)
    result = disable_file_context(url, token, args.model_id)
    print(f"file_context abgeschaltet ({result['aktion']}) für {result['model_id']}.")


if __name__ == "__main__":
    main()
