"""
Scan a local Joern source checkout for public CPGQL step definitions.

This is raw-material collection, not understanding: a regex pass over the traversal-DSL
modules (``language/`` — the ``io.shiftleft/joern`` step-definition packages — and
``semanticcpg``, where most ``Steps``/traversal-source classes live) that pulls out every
public ``def``/``implicit class`` with its preceding Scaladoc, if any. The LLM extraction
pass (``agent/dslgen/extract.py``) is what turns this into a structured API catalog — the
scan just needs to not miss things, not be a Scala parser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

#: only scan under source directories that plausibly define traversal-DSL steps
_RELEVANT_DIR_HINTS = ("language", "semanticcpg", "dataflowengineoss")

#: a Scaladoc block (non-greedy), then blank/comment lines, then a def/implicit-class decl.
_DOC_DEF_RE = re.compile(
    r"(?:/\*\*(?P<doc>.*?)\*/\s*)?"
    r"^\s*(?:override\s+)?(?:implicit\s+)?(?P<kind>def|class)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<sig>[^\n{=]*)",
    re.DOTALL | re.MULTILINE,
)

_PRIVATE_RE = re.compile(r"^\s*private\b")


def _clean_doc(doc: str) -> str:
    lines = [ln.strip().lstrip("*").strip() for ln in doc.splitlines()]
    return " ".join(ln for ln in lines if ln)


def _is_relevant_file(path: Path, joern_src: Path) -> bool:
    rel = path.relative_to(joern_src).as_posix().lower()
    return any(hint in rel for hint in _RELEVANT_DIR_HINTS) and rel.endswith(".scala")


def _scan_file(path: Path, joern_src: Path) -> List[Dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rel = path.relative_to(joern_src).as_posix()
    out: List[Dict[str, str]] = []
    for m in _DOC_DEF_RE.finditer(text):
        name = m.group("name")
        if not name or name.startswith("_"):
            continue
        # the line the match starts on — reject private/protected members
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.start())
        decl_line = text[line_start:line_end if line_end != -1 else len(text)]
        if _PRIVATE_RE.match(decl_line) or "protected" in decl_line.split(m.group("kind"))[0]:
            continue
        sig = re.sub(r"\s+", " ", (m.group("sig") or "")).strip()
        out.append({
            "name": name,
            "kind": m.group("kind"),
            "signature": f"{m.group('kind')} {name}{sig}".strip(),
            "doc": _clean_doc(m.group("doc") or ""),
            "file": rel,
        })
    return out


def scan(joern_src: Path) -> List[Dict[str, str]]:
    """
    Return every public ``def``/``class`` (candidate traversal step) found under
    ``joern_src``'s DSL source directories, deduplicated by (name, signature).
    """
    joern_src = Path(joern_src).expanduser().resolve()
    if not joern_src.is_dir():
        raise FileNotFoundError(f"joern source checkout not found: {joern_src}")

    seen = set()
    results: List[Dict[str, str]] = []
    for path in sorted(joern_src.rglob("*.scala")):
        rel = path.relative_to(joern_src).as_posix().lower()
        if "/target/" in rel or "/test" in rel:
            continue
        if not _is_relevant_file(path, joern_src):
            continue
        for entry in _scan_file(path, joern_src):
            key = (entry["name"], entry["signature"])
            if key in seen:
                continue
            seen.add(key)
            results.append(entry)
    return results
