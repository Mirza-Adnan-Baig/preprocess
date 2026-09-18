"""
Diagnostic Pipe Function for Open WebUI.

Install this FIRST, before the real extraction pipeline. It doesn't do any
extraction — it just shows you exactly what file data Open WebUI actually
hands to a Pipe Function when you upload a file, so we can confirm whether
we get the raw file (what we need) or Open WebUI's own pre-extracted text
(which would defeat the purpose of this whole project).

Install: Open WebUI admin -> Admin Panel -> Functions -> Create
  - Paste this entire file into the code editor
  - Save, then flip the "Active" toggle on
  - In a new chat, select "Diagnostic: File Inspector" as the model
  - Upload a real invoice/inventory file and ask any question
  - Read the JSON dump it prints back — send it back so the real pipeline
    can be built against what's actually there
"""

import json
from pydantic import BaseModel


class Pipe:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.id = "diagnostic_file_inspector"
        self.name = "Diagnostic: File Inspector"
        self.valves = self.Valves()

    async def pipe(self, body: dict, __files__: list = None, __user__: dict = None) -> str:
        report = {
            "body_top_level_keys": list(body.keys()),
            "num_files": len(__files__) if __files__ else 0,
            "files": [],
        }

        if __files__:
            for f in __files__:
                file_info = f.get("file", {})
                data = file_info.get("data", {})
                content = data.get("content")

                report["files"].append({
                    "top_level_keys": list(f.keys()),
                    "file_dict_keys": list(file_info.keys()),
                    "filename": file_info.get("filename"),
                    "meta": file_info.get("meta"),
                    "content_type": type(content).__name__,
                    "content_length": len(content) if content else 0,
                    "content_preview_first_300_chars": (
                        content[:300] if isinstance(content, str) else None
                    ),
                })

        return (
            "```json\n"
            + json.dumps(report, indent=2, default=str, ensure_ascii=False)
            + "\n```\n\n"
            "Copy everything above (including the JSON) and send it back — "
            "this tells us exactly what data the real pipeline can work with."
        )
