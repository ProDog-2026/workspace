"""Shared tool schemas and side-effect-free responses for the mock tools."""
import json
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
NAMES = frozenset(item["name"] for item in CATALOG)


def definitions():
    return [{"type": "function", "function": {
        "name": item["name"], "description": item["description"],
        "parameters": {"type": "object",
                       "properties": {field: {"type": "string"} for field in item["fields"]},
                       "required": item["fields"], "additionalProperties": False},
    }} for item in CATALOG]


def response():
    return {"simulated": True, "performed": False,
            "message": "Mock request recorded. No real action was performed."}
