#!/usr/bin/env python3
"""
A real Language Server Protocol server for Joern/CPGQL — diagnostics + hover only.

Joern has no LSP of its own (unlike CodeQL, which ships a real one that `codeql-lsp-mcp`
wraps). This is the Joern-side equivalent: standard LSP wire framing (Content-Length headers
over stdio), backed by a persistent `joern --server` session (`agent/engines/_joern_server.py`).

Capabilities:
  textDocumentSync   full-document sync; every didOpen/didChange re-validates and publishes
                     structured diagnostics (real line/column ranges, parsed from Joern's
                     Scala 3 compiler error text — see diagnostics.py)
  hoverProvider      the step under the cursor, looked up in the curated DSL catalog
                     (agent/prompts/joern_dsl.txt) — see hover.py

Config: JOERN_HOME env (dir with `joern`); optional MOCQ_CPG env (a cpg.bin to importCpg so
diagnostics resolve node/step types against real data, exactly like joern-mcp's joern_compile).

This is a plain LSP server — connect any LSP client to it (stdio). `joern-mcp/server.py`
bridges it into an MCP tool surface for the synthesis agent sessions.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from diagnostics import parse_diagnostics  # noqa: E402
from hover import hover_markdown, load_catalog, word_at  # noqa: E402
from lsp_wire import read_message, write_message  # noqa: E402
from agent.engines._joern_server import JoernServer  # noqa: E402

_DSL_PATH = _ROOT.parent / "agent" / "prompts" / "joern_dsl.txt"
_OK_MARKER = "__JOERN_LSP_OK__"

_documents: dict[str, str] = {}
_catalog = load_catalog(_DSL_PATH)
_server: Optional[JoernServer] = None


def _joern_bin() -> str:
    home = os.environ.get("JOERN_HOME", "")
    return str(Path(home) / "joern") if home else "joern"


def _ensure_server() -> JoernServer:
    global _server
    if _server is None:
        s = JoernServer(_joern_bin())
        s.start()
        cpg = os.environ.get("MOCQ_CPG")
        if cpg and Path(cpg).is_file():
            s.import_cpg(Path(cpg))
        _server = s
    return _server


def _validate(text: str) -> list:
    """Wrap `text` exactly like diagnostics.parse_diagnostics expects (1 prefix line)."""
    try:
        srv = _ensure_server()
    except Exception as exc:  # noqa: BLE001
        return [{
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
            "severity": 1, "message": f"could not start joern server: {exc}", "source": "joern",
        }]
    block = "{\n" + text + f'\n"{_OK_MARKER}"\n}}'
    try:
        out = srv.execute(block, timeout=180)["stdout"]
    except Exception as exc:  # noqa: BLE001
        return [{
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
            "severity": 1, "message": f"joern server error: {exc}", "source": "joern",
        }]
    return parse_diagnostics(out, wrap_prefix_lines=1)


# --------------------------------------------------------------------------- #
# message helpers
# --------------------------------------------------------------------------- #

def _reply(stream, mid, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    write_message(stream, msg)


def _notify(stream, method: str, params: dict) -> None:
    write_message(stream, {"jsonrpc": "2.0", "method": method, "params": params})


def _publish_diagnostics(stream, uri: str) -> None:
    text = _documents.get(uri, "")
    diags = _validate(text)
    _notify(stream, "textDocument/publishDiagnostics", {"uri": uri, "diagnostics": diags})


# --------------------------------------------------------------------------- #
# main loop
# --------------------------------------------------------------------------- #

def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        msg = read_message(stdin)
        if msg is None:
            break
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}

        if method == "initialize":
            _reply(stdout, mid, {
                "capabilities": {
                    "textDocumentSync": 1,  # Full
                    "hoverProvider": True,
                },
                "serverInfo": {"name": "joern-lsp", "version": "0.1.0"},
            })
        elif method == "initialized":
            pass
        elif method == "textDocument/didOpen":
            doc = params.get("textDocument", {})
            uri = doc.get("uri", "")
            _documents[uri] = doc.get("text", "")
            _publish_diagnostics(stdout, uri)
        elif method == "textDocument/didChange":
            doc = params.get("textDocument", {})
            uri = doc.get("uri", "")
            changes = params.get("contentChanges", [])
            if changes:
                # full-sync only: last change's `text` is the whole new document
                _documents[uri] = changes[-1].get("text", _documents.get(uri, ""))
            _publish_diagnostics(stdout, uri)
        elif method == "textDocument/didClose":
            uri = params.get("textDocument", {}).get("uri", "")
            _documents.pop(uri, None)
        elif method == "textDocument/hover":
            uri = params.get("textDocument", {}).get("uri", "")
            pos = params.get("position", {})
            text = _documents.get(uri, "")
            word = word_at(text, pos.get("line", 0), pos.get("character", 0))
            md = hover_markdown(_catalog, word)
            if md:
                _reply(stdout, mid, {"contents": {"kind": "markdown", "value": md}})
            else:
                _reply(stdout, mid, None)
        elif method == "shutdown":
            _reply(stdout, mid, None)
        elif method == "exit":
            break
        elif mid is not None:
            _reply(stdout, mid, error={"code": -32601, "message": f"unknown method {method}"})
        # unknown notifications (mid is None) are silently ignored per the LSP spec


if __name__ == "__main__":
    try:
        main()
    finally:
        if _server is not None:
            _server.stop()
