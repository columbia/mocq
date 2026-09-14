"""
Scan a local CodeQL standard-library checkout for public taint-tracking-relevant QL
declarations (classes, predicates, and value-returning accessor methods), for one language.

Same spirit as ``joern_source_scan.py`` but for QL syntax: a regex pass over the
dataflow/security library modules for one language (``<lang>/ql/lib/**/*.qll``) that pulls
out every public declaration with its preceding QLDoc, if any. Raw-material collection only —
the LLM extraction pass turns this into a structured catalog.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

#: only scan under library directories that plausibly define source/sink/taint concepts
_RELEVANT_DIR_HINTS = ("dataflow", "security", "concepts", "frameworks")

_LANG_LIB_DIR = {
    "python": "python/ql/lib",
    "javascript": "javascript/ql/lib",
    "typescript": "javascript/ql/lib",
    "java": "java/ql/lib",
}

_MODS = r"(?:abstract\s+|private\s+|final\s+|override\s+|bindingset\[[^\]]*\]\s*|pragma\[[^\]]*\]\s*)*"

# class/predicate declaration (boolean-valued members require the `predicate` keyword)
_DECL_RE = re.compile(
    rf"^\s*(?P<mods>{_MODS})(?P<kind>class|predicate)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<sig>[^\n{]*)",
    re.MULTILINE,
)

# a value-returning accessor member is declared return-type-first, with NO `predicate`/`class`
# keyword at all (e.g. `abstract DataFlow::Node getAQueryArgument();`) — often exactly the
# accessor needed to pull a concrete sink/source expression out of a concept class (e.g.
# DatabaseAccess.getAQueryArgument()), so scan for it separately: a QL type name (starts
# uppercase, or a lowercase primitive) followed by a lowerCamelCase method name and a
# parameter list ending in `;` or `{` — the parens+terminator requirement keeps this from
# matching ordinary field references or local variable declarations.
_ACCESSOR_DECL_RE = re.compile(
    rf"^\s*(?P<mods>{_MODS})"
    r"(?P<type>(?:boolean|string|int|float|date)\b|[A-Z][A-Za-z0-9_]*(?:::[A-Za-z0-9_]+)*"
    r"(?:<[^>\n]*>)?)\s+"
    r"(?P<name>[a-z][A-Za-z0-9_]*)\s*\((?P<sig>[^()\n]*)\)\s*[;{]",
    re.MULTILINE,
)

_DOC_BLOCK_RE = re.compile(r"/\*\*(?P<doc>.*?)\*/", re.DOTALL)


def _clean_doc(doc: str) -> str:
    lines = [ln.strip().lstrip("*").strip() for ln in doc.splitlines()]
    return " ".join(ln for ln in lines if ln)


def _preceding_doc(text: str, pos: int, lookback: int = 2000) -> str:
    """
    The QLDoc comment immediately (whitespace-only gap) preceding position `pos`, or "" if
    none. Deliberately NOT a single backtracking regex anchored with \\Z: `re.search` prefers
    the *leftmost* match, so a `/\\*\\*...\\*/\\s*\\Z` pattern would happily stretch its non-greedy
    `.*?` across several unrelated intervening doc blocks to reach the one adjacent comment,
    concatenating them all. Instead: enumerate every doc block in the lookback window and take
    the last one, then only accept it if nothing but whitespace follows it up to `pos`.
    """
    window = text[max(0, pos - lookback):pos]
    last = None
    for m in _DOC_BLOCK_RE.finditer(window):
        last = m
    if last is None or window[last.end():].strip():
        return ""
    return _clean_doc(last.group("doc"))


def _is_relevant_file(path: Path, lib_root: Path) -> bool:
    rel = path.relative_to(lib_root).as_posix().lower()
    return any(hint in rel for hint in _RELEVANT_DIR_HINTS) and rel.endswith(".qll")


def _scan_file(path: Path, lib_root: Path) -> List[Dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rel = path.relative_to(lib_root).as_posix()
    out: List[Dict[str, str]] = []

    for m in _DECL_RE.finditer(text):
        name = m.group("name")
        if not name or name.startswith("_") or "private" in m.group("mods"):
            continue
        sig = re.sub(r"\s+", " ", (m.group("sig") or "")).strip()
        signature = f"{m.group('kind')} {name} {sig}".strip() if sig else f"{m.group('kind')} {name}"
        out.append({
            "name": name,
            "kind": m.group("kind"),
            "signature": signature,
            "doc": _preceding_doc(text, m.start()),
            "file": rel,
        })

    for m in _ACCESSOR_DECL_RE.finditer(text):
        name = m.group("name")
        if not name or name.startswith("_") or "private" in m.group("mods"):
            continue
        sig = re.sub(r"\s+", " ", (m.group("sig") or "")).strip()
        signature = f"{m.group('type')} {name}({sig})"
        out.append({
            "name": name,
            "kind": "accessor",
            "signature": signature,
            "doc": _preceding_doc(text, m.start()),
            "file": rel,
        })
    return out


def scan(codeql_repo: Path, language: str) -> List[Dict[str, str]]:
    """
    Return every public class/predicate/accessor declaration found under `language`'s
    dataflow/security/concepts library directories in `codeql_repo`, deduplicated by
    (name, signature).
    """
    codeql_repo = Path(codeql_repo).expanduser().resolve()
    lib_dir = _LANG_LIB_DIR.get(language)
    if lib_dir is None:
        raise ValueError(f"unsupported language {language!r} for CodeQL DSL extraction")
    lib_root = codeql_repo / lib_dir
    if not lib_root.is_dir():
        raise FileNotFoundError(f"CodeQL library dir not found: {lib_root}")

    seen = set()
    results: List[Dict[str, str]] = []
    for path in sorted(lib_root.rglob("*.qll")):
        rel_lower = path.relative_to(lib_root).as_posix().lower()
        if "/test/" in rel_lower or rel_lower.startswith("test/"):
            continue
        if not _is_relevant_file(path, lib_root):
            continue
        for entry in _scan_file(path, lib_root):
            key = (entry["name"], entry["signature"])
            if key in seen:
                continue
            seen.add(key)
            results.append(entry)
    return results
