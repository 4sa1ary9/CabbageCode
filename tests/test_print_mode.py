import asyncio
import json

from cabbagecode import client as client_mod
from cabbagecode.__main__ import _run_prompt
from cabbagecode.client import LLMClient
from cabbagecode.config import load_config
from cabbagecode.permissions import PermissionMode
from cabbagecode.tools.base import StreamEnd, TextDelta, ToolCallComplete

NL = chr(10)


class ScriptedClient(LLMClient):
    def __init__(self, responses):
        self._responses = list(responses)

    async def stream(self, conversation, system="", tools=None):
        events = self._responses.pop(0) if self._responses else [TextDelta("done"), StreamEnd("end_turn")]
        for e in events:
            yield e


def _setup(tmp_path, monkeypatch, responses):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cabbagecode").mkdir()
    (tmp_path / ".cabbagecode" / "config.yaml").write_text(NL.join([
        "providers:", "  - name: fake", "    protocol: anthropic", "    base_url: http://127.0.0.1:9",
        "    api_key: x", "    model: fake-model", ""]), encoding="utf-8")
    monkeypatch.setattr(client_mod, "create_client", lambda provider: ScriptedClient(responses))

    async def no_fetch(provider):
        return None

    monkeypatch.setattr(client_mod, "resolve_context_window", no_fetch)
    return load_config()


def _events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_print_mode_denies_permission_and_stops_at_max_turns(tmp_path, monkeypatch, capsys):
    write = [ToolCallComplete("w1", "WriteFile", {"file_path": str(tmp_path / "x.txt"), "content": "hi"}),
             StreamEnd("tool_use", input_tokens=1, output_tokens=1)]
    config = _setup(tmp_path, monkeypatch, [write, write, write])

    rc = asyncio.run(_run_prompt(config, PermissionMode.DEFAULT, None, "write x.txt", "stream-json", max_turns=2))

    events = _events(capsys)
    assert rc == 1
    assert any(e["type"] == "permission_denied" and e["tool_name"] == "WriteFile" for e in events)
    assert not (tmp_path / "x.txt").exists()
    result = [e for e in events if e["type"] == "result"][-1]
    assert result["is_error"] and result["stop_reason"] == "max_turns"


def test_print_mode_exits_zero_on_normal_completion(tmp_path, monkeypatch, capsys):
    config = _setup(tmp_path, monkeypatch, [[TextDelta("hello"), StreamEnd("end_turn", input_tokens=2, output_tokens=1)]])

    rc = asyncio.run(_run_prompt(config, PermissionMode.DEFAULT, None, "hi", "stream-json"))

    result = [e for e in _events(capsys) if e["type"] == "result"][-1]
    assert rc == 0
    assert result["is_error"] is False and result["stop_reason"] == "end_turn"
