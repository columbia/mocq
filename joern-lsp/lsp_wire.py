"""Minimal LSP wire framing (Content-Length headers over a byte stream) — shared by the
joern-lsp server and any client that talks to it (joern-mcp bridges through this)."""

from __future__ import annotations

import json
from typing import Optional


def read_message(stream) -> Optional[dict]:
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.decode("ascii", errors="replace").rstrip("\r\n")
        if line == "":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = stream.read(length)
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def write_message(stream, msg: dict) -> None:
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stream.write(header + body)
    stream.flush()
