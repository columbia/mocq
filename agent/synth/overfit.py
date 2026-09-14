"""
Generalization for recall (MoCQ paper §3.3.2) — heuristics that flag a query for overfitting
to the specific examples it was built from (hardcoded literals/identifiers, or overly-specific
structural matches), so it can be asked to generalize before being treated as final. Engine-
neutral: operates on query text + the example source files, not on any engine's AST.
"""

from __future__ import annotations

import re
from typing import Dict, List

# generic terms that appear in almost any query for these vulnerability classes — never flag
# these as "hardcoded to one example" even though they're short/common tokens.
_STOPWORDS = {
    "get", "post", "put", "delete", "head", "options", "request", "response",
    "query", "execute", "exec", "argument", "call", "method", "true", "false",
    "null", "none", "self", "this", "index", "value", "name", "code",
}

# an exact (non-regex-special) string literal passed to a code/name-matching step
_LITERAL_IN_MATCH_RE = re.compile(
    r'\.(?:code|name|nameExact|methodFullName)\(\s*"([A-Za-z_][A-Za-z0-9_]{2,})"\s*\)'
)
_LINE_FILTER_RE = re.compile(
    r"\.lineNumber\(\s*\d+\s*\)|(?:getStartLine\(\)|getLocation\(\)\.getStartLine\(\))\s*=\s*\d+"
)
_ASTPARENT_CHAIN_RE = re.compile(r"(?:\.astParent){4,}")

# Joern uses traversal matchers; CodeQL commonly expresses the same exact
# constraint as a QL predicate call or equality.  Keep these intentionally
# narrow so ordinary framework/API names do not trigger generalization.
_CODEQL_LITERAL_IN_MATCH_RE = re.compile(
    r"(?:\.(?:hasName|hasQualifiedName|getName|getQualifiedName)\(\s*|"
    r"(?:getName|getQualifiedName)\(\)\s*=\s*)\"([A-Za-z_][A-Za-z0-9_]{2,})\""
)


def _relevant_block(query_text: str) -> str:
    """The mocqSource/mocqSink/mocqSanitizer bodies — where overfitting actually matters."""
    m = re.search(
        r"\b(?:def|predicate)\s+mocq(?:Source|Sink|Sanitizer|Barrier)\b.*",
        query_text,
        re.DOTALL,
    )
    return m.group(0) if m else query_text


def detect_overfit_signals(query_text: str, example_files: Dict[str, str]) -> List[str]:
    """
    Return a list of short, human-readable overfit warnings, or [] if none found.
    `example_files` maps a corpus-relative path to that file's source text.
    """
    block = _relevant_block(query_text)
    signals: List[str] = []

    if _LINE_FILTER_RE.search(block):
        signals.append("uses an exact `.lineNumber(N)` filter — overfits to one file's line numbers")
    if _ASTPARENT_CHAIN_RE.search(block):
        signals.append("uses a long `.astParent` chain — overfits to one file's AST shape")

    for m in _LITERAL_IN_MATCH_RE.finditer(block):
        token = m.group(1)
        if token.lower() in _STOPWORDS or len(token) < 4:
            continue
        hits = [f for f, text in example_files.items() if token in text]
        if len(hits) == 1:
            signals.append(
                f'`"{token}"` is an exact literal/identifier that only appears in {hits[0]} — '
                f"generalize this into a framework/API-level condition"
            )

    for m in _CODEQL_LITERAL_IN_MATCH_RE.finditer(block):
        token = m.group(1)
        if token.lower() in _STOPWORDS or len(token) < 4:
            continue
        hits = [f for f, text in example_files.items() if token in text]
        if len(hits) == 1:
            signals.append(
                f'`"{token}"` is an exact CodeQL name literal that only appears in {hits[0]} — '
                f"generalize this into a framework/API-level condition"
            )

    seen, out = set(), []
    for s in signals:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
