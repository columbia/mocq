"""
Hover support for the Joern LSP: parses agent/prompts/joern_dsl.txt's "Subset catalog" bullet
list into a step-name -> (signature, description) map, and finds the identifier under a given
cursor position in a document.

Catalog lines look like:
    - `def reachableByFlows[A](sourceTrav: IterableOnce[A]*)(implicit ctx: EngineContext): Traversal[Path]` — Find complete data-flow paths ...
    - `cpg.call: Traversal[Call]` — All call sites; typical starting point for sinks/sources by callee name.
    - `cpg: Cpg` — Root object referencing the Code Property Graph being queried.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Tuple

_BULLET_RE = re.compile(r"^-\s*`(?P<sig>[^`]+)`\s*(?:—|--)\s*(?P<desc>.+)$")
_DEF_NAME_RE = re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load_catalog(dsl_path: Path) -> Dict[str, Tuple[str, str]]:
    """Return {step_name: (signature, description)} parsed from a joern_dsl.txt-shaped file."""
    catalog: Dict[str, Tuple[str, str]] = {}
    if not dsl_path.is_file():
        return catalog
    for line in dsl_path.read_text(encoding="utf-8").splitlines():
        m = _BULLET_RE.match(line.strip())
        if not m:
            continue
        sig, desc = m.group("sig").strip(), m.group("desc").strip()
        name_match = _DEF_NAME_RE.match(sig)
        if name_match:
            name = name_match.group(1)
        else:
            # "cpg.call: Traversal[Call]" -> "call"; "cpg: Cpg" -> "cpg"
            head = sig.split(":", 1)[0].strip()
            name = head.rsplit(".", 1)[-1]
        if name and name not in catalog:
            catalog[name] = (sig, desc)
    return catalog


def word_at(text: str, line: int, character: int) -> Optional[str]:
    """The identifier touching 0-indexed (line, character) in `text`, or None."""
    lines = text.splitlines()
    if line < 0 or line >= len(lines):
        return None
    row = lines[line]
    for m in _IDENT_RE.finditer(row):
        if m.start() <= character <= m.end():
            return m.group(0)
    return None


def hover_markdown(catalog: Dict[str, Tuple[str, str]], word: Optional[str]) -> Optional[str]:
    if not word or word not in catalog:
        return None
    sig, desc = catalog[word]
    return f"```scala\n{sig}\n```\n\n{desc}"
