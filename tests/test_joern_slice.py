"""
Unit tests for the lightweight, server-driven JoernEngine.slice_file (no `joern-slice`
CLI, no subprocess) — verifies the query-building/escaping and result parsing without a
live Joern server.
"""

from pathlib import Path

from agent.engines.joern import JoernEngine


class _FakeServer:
    def __init__(self, stdout: str = "", raise_exc: Exception | None = None):
        self.stdout = stdout
        self.raise_exc = raise_exc
        self.last_query = None
        self.last_timeout = None

    def execute(self, query: str, timeout: int = 900) -> dict:
        self.last_query = query
        self.last_timeout = timeout
        if self.raise_exc:
            raise self.raise_exc
        return {"stdout": self.stdout, "stderr": "", "success": True}


def _engine_with_server(server) -> JoernEngine:
    engine = JoernEngine(joern_home="")
    engine._server = server
    return engine


def test_slice_file_returns_none_when_server_not_open():
    engine = JoernEngine(joern_home="")
    assert engine._server is None
    assert engine.slice_file(Path("/tmp/db"), "vuln/a.php") is None


def test_slice_file_escapes_target_path_into_query():
    server = _FakeServer(stdout='val res0: String = "{\\"nodes\\":[],\\"edges\\":[]}"')
    engine = _engine_with_server(server)
    engine.slice_file(Path("/tmp/db"), 'vuln/weird"path.php')
    assert '__target = "vuln/weird\\"path.php"' in server.last_query
    assert "REACHING_DEF" in server.last_query and "CDG" in server.last_query


def test_slice_file_parses_valid_json_result():
    payload = (
        '{"nodes":[{"id":1,"label":"CALL","code":"sink(x)","name":"sink",'
        '"lineNumber":3,"parentMethod":"a.php:<global>"}],"edges":[]}'
    )
    # the exact REPL echo shape _extract_json expects: outer quotes literal, inner escaped
    server = _FakeServer(stdout=f'val res0: String = "{payload.replace(chr(34), chr(92)+chr(34))}"')
    engine = _engine_with_server(server)
    result = engine.slice_file(Path("/tmp/db"), "a.php")
    assert result == {
        "nodes": [{"id": 1, "label": "CALL", "code": "sink(x)", "name": "sink",
                    "lineNumber": 3, "parentMethod": "a.php:<global>"}],
        "edges": [],
    }


def test_slice_file_returns_none_on_error_marker():
    server = _FakeServer(stdout="some noise\n-- [E008] Not Found Error: nope\nmore noise")
    engine = _engine_with_server(server)
    assert engine.slice_file(Path("/tmp/db"), "a.php") is None


def test_slice_file_returns_none_on_exception():
    server = _FakeServer(raise_exc=TimeoutError("server hung"))
    engine = _engine_with_server(server)
    assert engine.slice_file(Path("/tmp/db"), "a.php") is None


def test_slice_file_returns_none_on_non_dict_json():
    server = _FakeServer(stdout='val res0: String = "[1,2,3]"')
    engine = _engine_with_server(server)
    assert engine.slice_file(Path("/tmp/db"), "a.php") is None


def test_slice_file_passes_timeout_through():
    server = _FakeServer(stdout='val res0: String = "{\\"nodes\\":[],\\"edges\\":[]}"')
    engine = _engine_with_server(server)
    engine.slice_file(Path("/tmp/db"), "a.php", timeout=15)
    assert server.last_timeout == 15
