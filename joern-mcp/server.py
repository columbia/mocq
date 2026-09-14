#!/usr/bin/env python3
"""
Minimal stdio MCP server for Joern — the CPGQL analogue of codeql-lsp-mcp.

Joern has no LSP of its own, so `../joern-lsp/server.py` provides one (diagnostics + hover,
real line/column ranges parsed from Joern's Scala compiler output). This MCP server is an LSP
*client* for that server — exactly the role codeql-lsp-mcp plays for CodeQL's real LSP — giving
the synthesis agent session tools comparable to codeql_compile / codeql_hover.

Tools
  joern_compile   evaluate a CPGQL snippet via joern-lsp, report structured errors/warnings
  joern_dsl       the CPGQL DSL reference (optionally filtered to a keyword)

Config: JOERN_HOME env (dir with `joern`); optional MOCQ_CPG env (a .cpg.bin to
importCpg so compile-checking resolves node/step types) — both forwarded to joern-lsp.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_LSP_DIR = _ROOT.parent / "joern-lsp"
sys.path.insert(0, str(_LSP_DIR))
from lsp_wire import read_message, write_message  # noqa: E402

_DSL = (_ROOT.parent / "agent" / "prompts" / "joern_dsl.txt")
_LSP_URI = "file:///mocq/query.sc"

_lsp_proc: subprocess.Popen | None = None
_lsp_opened = False


def _ensure_lsp() -> subprocess.Popen:
    """Lazily spawn (or respawn, if it died) the joern-lsp server and do its handshake."""
    global _lsp_proc, _lsp_opened
    if _lsp_proc is not None and _lsp_proc.poll() is None:
        return _lsp_proc
    _lsp_proc = subprocess.Popen(
        [sys.executable, str(_LSP_DIR / "server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    )
    write_message(_lsp_proc.stdin, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    read_message(_lsp_proc.stdout)  # discard the initialize result
    write_message(_lsp_proc.stdin, {"jsonrpc": "2.0", "method": "initialized", "params": {}})
    _lsp_opened = False
    return _lsp_proc


def _lsp_diagnostics(query: str) -> list:
    """Push `query` as the LSP document's full text and return the resulting diagnostics."""
    global _lsp_opened
    proc = _ensure_lsp()
    if not _lsp_opened:
        write_message(proc.stdin, {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {
            "textDocument": {"uri": _LSP_URI, "languageId": "scala", "version": 1, "text": query},
        }})
        _lsp_opened = True
    else:
        write_message(proc.stdin, {"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {
            "textDocument": {"uri": _LSP_URI, "version": 2},
            "contentChanges": [{"text": query}],
        }})
    msg = read_message(proc.stdout)
    if msg is None:
        raise RuntimeError("joern-lsp closed the connection")
    return msg.get("params", {}).get("diagnostics", [])


def _fmt_diag(d: dict) -> str:
    start = d.get("range", {}).get("start", {})
    kind = "warning" if d.get("severity") == 2 else "error"
    return f"line {start.get('line', 0) + 1}, col {start.get('character', 0) + 1} [{kind}]: {d.get('message', '')}"


def _tool_joern_compile(args: dict) -> str:
    query = (args or {}).get("query", "")
    if not query.strip():
        return "error: `query` is required"
    try:
        diagnostics = _lsp_diagnostics(query.strip())
    except Exception as exc:  # noqa: BLE001
        global _lsp_proc
        _lsp_proc = None  # force a respawn on the next call
        return f"joern-lsp error: {exc}"
    errors = [d for d in diagnostics if d.get("severity") == 1]
    if errors:
        return "FAILED:\n" + "\n".join(_fmt_diag(d) for d in errors)
    if diagnostics:
        return "OK — compiles and evaluates cleanly, with warning(s):\n" + "\n".join(
            _fmt_diag(d) for d in diagnostics)
    return "OK — compiles and evaluates cleanly."


def _tool_joern_dsl(args: dict) -> str:
    text = _DSL.read_text(encoding="utf-8") if _DSL.exists() else "(joern_dsl.txt missing)"
    kw = (args or {}).get("keyword", "").strip()
    if not kw:
        return text
    hits = [ln for ln in text.splitlines() if kw.lower() in ln.lower()]
    return "\n".join(hits) if hits else f"no DSL lines match {kw!r}"


_TOOLS = {
    "joern_compile": {
        "description": "Evaluate a CPGQL (Scala) snippet against a joern server and report syntax / "
                       "runtime errors. Returns 'OK' or the error region.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "CPGQL snippet (def ... / expression)"}},
            "required": ["query"],
        },
        "handler": _tool_joern_compile,
    },
    "joern_dsl": {
        "description": "The curated CPGQL DSL reference (grammar + step catalog). Pass `keyword` to "
                       "filter to matching lines.",
        "inputSchema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
        },
        "handler": _tool_joern_dsl,
    },
}


def _reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method, mid, params = req.get("method"), req.get("id"), req.get("params") or {}
        if method == "initialize":
            _reply(mid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "joern-mcp", "version": "0.1.0"},
            })
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _reply(mid, {"tools": [
                {"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
                for n, t in _TOOLS.items()
            ]})
        elif method == "tools/call":
            name = params.get("name")
            tool = _TOOLS.get(name)
            if not tool:
                _reply(mid, error={"code": -32601, "message": f"unknown tool {name}"})
                continue
            try:
                text = tool["handler"](params.get("arguments") or {})
            except Exception as exc:  # noqa: BLE001
                text = f"tool error: {exc}"
            _reply(mid, {"content": [{"type": "text", "text": text}]})
        elif mid is not None:
            _reply(mid, error={"code": -32601, "message": f"unknown method {method}"})


if __name__ == "__main__":
    try:
        main()
    finally:
        if _lsp_proc is not None and _lsp_proc.poll() is None:
            try:
                write_message(_lsp_proc.stdin, {"jsonrpc": "2.0", "id": 1, "method": "shutdown", "params": {}})
                read_message(_lsp_proc.stdout)
                write_message(_lsp_proc.stdin, {"jsonrpc": "2.0", "method": "exit", "params": {}})
            except Exception:  # noqa: BLE001
                pass
            _lsp_proc.terminate()
