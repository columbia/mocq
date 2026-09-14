"""
Unit tests for run_text_completion's per-provider command construction and result
extraction (agent/synth/session.py). No live CLI invoked: subprocess.Popen and
resolve_executable are stubbed.
"""

import json

import agent.synth.session as session
from agent.synth.session import _extract_text_result, run_text_completion


class _FakeProc:
    def __init__(self, out: bytes, err: bytes = b"", returncode: int = 0):
        self._out, self._err, self.returncode = out, err, returncode

    def communicate(self, input=None, timeout=None):
        return self._out, self._err


def _patch(monkeypatch, cmd_name, out=b'{"result": "PONG"}'):
    captured = {}

    def fake_resolve(provider):
        return f"/usr/bin/{cmd_name}", cmd_name

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, env=None, start_new_session=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProc(out)

    monkeypatch.setattr(session, "resolve_executable", fake_resolve)
    monkeypatch.setattr(session.subprocess, "Popen", fake_popen)
    return captured


def test_claude_cli_command_shape(monkeypatch):
    captured = _patch(monkeypatch, "claude")
    result = run_text_completion(provider="claude_cli", prompt="hi", model="opus")
    assert captured["cmd"][0] == "/usr/bin/claude"
    assert "--print" in captured["cmd"]
    assert "--output-format" in captured["cmd"] and "json" in captured["cmd"]
    assert "--model" in captured["cmd"] and "opus" in captured["cmd"]
    assert result == "PONG"


def test_agy_cli_command_shape(monkeypatch):
    captured = _patch(monkeypatch, "agy", out=b"PONG")
    result = run_text_completion(provider="agy_cli", prompt="hi there", timeout=42)
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/agy"
    assert "--dangerously-skip-permissions" in cmd
    assert "--print=hi there" in cmd
    assert "--print-timeout" in cmd and "42s" in cmd
    assert result == "PONG"


def test_gemini_cli_command_shape(monkeypatch):
    captured = _patch(monkeypatch, "gemini", out=b"PONG")
    result = run_text_completion(provider="gemini_cli", prompt="hi")
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/gemini"
    assert "--prompt" in cmd
    assert captured["env"]["GEMINI_SANDBOX"] == "false"
    assert result == "PONG"


def test_unsupported_provider_raises(monkeypatch):
    _patch(monkeypatch, "claude")
    try:
        run_text_completion(provider="not_a_provider", prompt="hi")
    except ValueError as exc:
        assert "unsupported model_provider" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_extract_text_result_json_envelope():
    assert _extract_text_result('{"result": " hello "}') == "hello"


def test_extract_text_result_ndjson_takes_last_result():
    raw = "\n".join([
        json.dumps({"type": "system", "info": "start"}),
        json.dumps({"type": "result", "result": "first"}),
        json.dumps({"type": "result", "result": "final answer"}),
    ])
    assert _extract_text_result(raw) == "final answer"


def test_extract_text_result_plain_text_passthrough():
    assert _extract_text_result("just plain text\n") == "just plain text"


def test_extract_text_result_empty():
    assert _extract_text_result("") == ""
