import json
from click.formatting import export_command_schema_json


def test_export_schema_excludes_hidden_by_default():
    opts = [
        {"name": "--verbose", "type": "bool", "default": False},
        {"name": "--debug-token", "type": "str", "hidden": True},
    ]
    raw = export_command_schema_json("deploy", opts)
    assert '\n  "command"' in raw
    data = json.loads(raw)
    assert data["command"] == "deploy"
    assert [o["name"] for o in data["options"]] == ["--verbose"]
    assert data["options"][0]["required"] is False


def test_export_schema_includes_hidden_when_requested_and_sorts():
    opts = [
        {"name": "--zeta", "required": True},
        {"name": "--alpha", "hidden": True, "default": "x"},
    ]
    data = json.loads(export_command_schema_json("run", opts, include_hidden=True))
    assert [o["name"] for o in data["options"]] == ["--alpha", "--zeta"]
    assert data["options"][0]["default"] == "x"
    assert data["options"][1]["required"] is True
