"""
Parse Joern's Scala 3 (dotty) REPL error/warning text into structured LSP diagnostics.

A block looks like:

    -- [E008] Not Found Error: -----------------------------------------------------
    2 |def mocqSource = cpg.call.nonExistentStepXYZ
      |                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^
      |value nonExistentStepXYZ is not a member of Iterator[...]

or, for a warning (no `[Exxx]` code):

    -- Deprecation Warning: --------------------------------------------------------
    3 |def mocqSink = 5 + "x"
      |               ^^^
      |method + in class Int is deprecated ...

Multiple such blocks can appear back to back (one compiler pass, several errors), ending
in a "N error(s)/warning(s) found" summary line we ignore. This module turns that text into
a list of ``Diagnostic`` dicts using LSP's own shape (``range``/``severity``/``message``),
line-shifted from "line N of the wrapped snippet" to "0-indexed line of the client's document".
"""

from __future__ import annotations

import re
from typing import List, Optional, TypedDict

_HEADER_RE = re.compile(r"^--\s*(?:\[(?P<code>E\d+)\]\s*)?(?P<kind>[^:]+):")
_SOURCE_LINE_RE = re.compile(r"^(?P<lineno>\d+)\s*\|(?P<text>.*)$")
_CARET_LINE_RE = re.compile(r"^\s*\|(?P<pad>\s*)(?P<carets>\^+)\s*$")
_MESSAGE_LINE_RE = re.compile(r"^\s*\|(?P<text>.*)$")
_SEPARATOR_RE = re.compile(r"^\s*[|-]?-{5,}\s*$")

SEVERITY_ERROR = 1
SEVERITY_WARNING = 2


class Position(TypedDict):
    line: int
    character: int


class Range(TypedDict):
    start: Position
    end: Position


class Diagnostic(TypedDict):
    range: Range
    severity: int
    message: str
    source: str


def _severity_for(kind: str) -> int:
    return SEVERITY_WARNING if "warning" in kind.lower() else SEVERITY_ERROR


def parse_diagnostics(raw: str, *, wrap_prefix_lines: int = 1) -> List[Diagnostic]:
    """
    Parse every diagnostic block in `raw`. `wrap_prefix_lines` is how many lines were
    prepended to the client's document before submitting it to Joern (the LSP server wraps
    the document as ``"{\\n" + text + ...``, i.e. 1 prefix line) — used to shift Joern's
    1-indexed "line in the wrapped snippet" back to a 0-indexed line in the client's own
    document.
    """
    lines = raw.splitlines()
    diagnostics: List[Diagnostic] = []
    i = 0
    n = len(lines)
    while i < n:
        header = _HEADER_RE.match(lines[i])
        if not header:
            i += 1
            continue
        severity = _severity_for(header.group("kind"))
        i += 1

        # find the "N |source text" line for this block
        src_match: Optional[re.Match] = None
        while i < n:
            src_match = _SOURCE_LINE_RE.match(lines[i])
            if src_match:
                break
            if _HEADER_RE.match(lines[i]):  # next block started with no source line — bail
                src_match = None
                break
            i += 1
        if src_match is None:
            continue
        wrapped_line = int(src_match.group("lineno"))
        i += 1

        # the caret-underline line right after it
        col_start, col_end = 0, len(src_match.group("text"))
        if i < n:
            caret = _CARET_LINE_RE.match(lines[i])
            if caret:
                col_start = len(caret.group("pad"))
                col_end = col_start + len(caret.group("carets"))
                i += 1

        # message lines: every subsequent "|..." line up to a separator / explanation / next block
        msg_parts: List[str] = []
        while i < n:
            if _HEADER_RE.match(lines[i]) or _SEPARATOR_RE.match(lines[i]):
                break
            m = _MESSAGE_LINE_RE.match(lines[i])
            if not m:
                break
            text = m.group("text").strip()
            if text.startswith("Explanation"):
                break
            if text:
                msg_parts.append(text)
            i += 1

        doc_line = wrapped_line - 1 - wrap_prefix_lines  # 1-indexed wrapped -> 0-indexed doc
        if doc_line < 0:
            continue  # error points into our own wrapper scaffolding, not the client's text
        diagnostics.append({
            "range": {
                "start": {"line": doc_line, "character": col_start},
                "end": {"line": doc_line, "character": max(col_end, col_start + 1)},
            },
            "severity": severity,
            "message": "\n".join(msg_parts) or header.group("kind").strip(),
            "source": "joern",
        })
    return diagnostics
