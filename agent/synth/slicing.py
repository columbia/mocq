"""
Program slicing per example (MoCQ paper §3.2, "MoCQ constructs a program slice for each
vulnerability example"). Joern-only: ``JoernEngine.slice_file`` runs a bounded,
interprocedural data-dependency slice directly against the already-open persistent
server (no subprocess), and this
module reconstructs a compact pseudocode rendering from its ``{"nodes": [...], "edges": [...]}``
payload (schema-compatible with, and originally ported from, the reference prototype's
``reconstruct_code.py`` for the real ``joern-slice`` CLI's output) — so the per-example prompt
can show a focused view instead of (or alongside) the raw file.

``slice_file`` seeds nodes in the target file, follows incoming data dependencies across
methods/files to a bounded depth. The stock
``joern-slice data-flow --file-filter`` CLI performs an unbounded walk and can become
non-terminating on a real CPG; explicit depth/node caps preserve interprocedural reach while
keeping preprocessing predictable. ``slice_file`` returns ``None`` on failure and callers must
treat that as "fall back to the raw file", not as an error.
"""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Dict, List, Optional


# A slice is produced before the generated query exists, so there is no exact
# source/sink predicate to seed it with.  These are deliberately broad,
# language-neutral indicators used only to discard obvious module boilerplate;
# the raw source is still included in the prompt as the lossless fallback.
_SECURITY_RE = re.compile(
    r"(?:\brequest\.(?:args|form|values|json|data|files|headers|cookies)\b|"
    r"\b(?:os\.environ|os\.getenv|sys\.argv|process\.(?:env|argv))\b|"
    r"\$_(?:GET|POST|REQUEST|COOKIE|FILES)\b|"
    # deliberately NOT \b-anchored before the keyword: a real dangerous call is very often a
    # suffix of a longer identifier (mysqli_query(...), PDO::query(...), $stmt->execute(...)),
    # and requiring a word boundary there made the whole method containing the actual
    # vulnerable call silently invisible to this filter (see test regression fixture).
    r"(?:input|execute|executemany|executescript|query|raw|system|popen|"
    r"check_output|check_call|open|readFile|writeFile|sendFile|innerHTML)\s*\()",
    re.IGNORECASE,
)


def _build_maps(nodes: List[dict], edges: List[dict]):
    id_to_node = {n["id"]: n for n in nodes}
    method_blocks: Dict[str, List[dict]] = defaultdict(list)
    for n in nodes:
        method_blocks[n.get("parentMethod", "<global>")].append(n)
    incoming: Dict[int, list] = defaultdict(list)
    for e in edges:
        incoming[e["dst"]].append((e["label"], e["src"]))
    return id_to_node, method_blocks, incoming


def _is_placeholder(nodes: List[dict]) -> bool:
    real = [n for n in nodes if n.get("label") not in {"METHOD_PARAMETER_IN", "METHOD"}]
    return not real


#: node kinds that can sensibly appear as the RHS text of a reconstructed assignment.
#: A REACHING_DEF edge from a METHOD node means "no real prior definition — this reaches
#: back to the enclosing method's entry" (an uninitialized parameter/global), not an actual
#: assignment; printing that node's `code` (the whole `function foo(...)` signature) verbatim
#: produces nonsense like `$db = VIRTUAL PUBLIC STATIC function <global>();`.
_ASSIGNMENT_SOURCE_LABELS = {"CALL", "LITERAL", "IDENTIFIER", "FIELD_IDENTIFIER", "METHOD_PARAMETER_IN"}


def _format_assignment(target_id: int, id_to_node: dict, incoming: dict) -> Optional[str]:
    sources = [
        src for label, src in incoming.get(target_id, [])
        if label == "REACHING_DEF" and id_to_node.get(src, {}).get("label") in _ASSIGNMENT_SOURCE_LABELS
    ]
    if not sources:
        return None
    source_code = id_to_node.get(sources[0], {}).get("code", "").strip()
    target_code = id_to_node.get(target_id, {}).get("code", "").strip()
    if source_code and target_code and source_code != target_code:
        return f"{target_code} = {source_code};"
    return None


def _format_body(nodes: List[dict], id_to_node: dict, incoming: dict) -> List[str]:
    lines: List[str] = []
    seen = set()
    emitted_text = set()
    stop = False
    # a call is "already rendered via its assignment target" only when its REACHING_DEF
    # destination is a real IDENTIFIER (handled by _format_assignment) — not when it's a
    # METHOD_RETURN sentinel (Joern links the last statement's value to method-exit even for
    # a bare, non-assigning statement call; treating that as "assigned" would drop the call).
    assigned_call_ids = {
        src for dst_id, dst_edges in incoming.items() for label, src in dst_edges
        if label == "REACHING_DEF"
        and id_to_node.get(src, {}).get("label") == "CALL"
        and id_to_node.get(dst_id, {}).get("label") == "IDENTIFIER"
    }

    def _emit(text: str) -> None:
        text = text.strip()
        if not text:
            return
        if not text.endswith(";"):
            text += ";"
        # Joern models one assignment as both an "<operator>.assignment" CALL (whose own
        # `code` is the full "lhs = rhs" text) AND a REACHING_DEF edge from the RHS call into
        # the LHS identifier (reconstructed separately by _format_assignment) — both paths
        # produce the same text for a plain assignment, so dedupe by final rendered text.
        if text not in emitted_text:
            emitted_text.add(text)
            lines.append(f"    {text}")

    for node in sorted(nodes, key=lambda n: n.get("lineNumber") or 1 << 30):
        nid = node["id"]
        if nid in seen or stop:
            continue
        seen.add(nid)
        label, code, name = node.get("label"), (node.get("code") or "").strip(), node.get("name", "")

        # Joern represents imports, decorators and compiler-generated helper
        # temporaries as CALL/IDENTIFIER nodes.  They are valid CPG nodes but
        # make a human/LLM-facing slice much noisier than the source itself.
        if label in {"METHOD_REF", "METHOD_RETURN", "BLOCK", "FIELD_IDENTIFIER"}:
            continue
        if label == "CALL" and (
            code.startswith("import(")
            or re.match(r"^(?:tmp\d+\.?|__tmp\d+)", code)
            or ".route(" in code
        ):
            continue

        if label == "RETURN":
            _emit(code)
            stop = True
        elif label == "IDENTIFIER":
            assignment = _format_assignment(nid, id_to_node, incoming)
            if assignment:
                _emit(assignment)
        elif label == "CALL":
            if nid in assigned_call_ids:
                continue
            if name == "<operator>.assignment" or not name.startswith("<operator>"):
                _emit(code)
    return lines


def reconstruct(slice_json: dict) -> str:
    """Render a joern-slice ``data-flow`` JSON payload as compact pseudocode."""
    nodes = slice_json.get("nodes", [])
    edges = slice_json.get("edges", [])
    if not nodes:
        return ""
    id_to_node, method_blocks, incoming = _build_maps(nodes, edges)

    # Prefer methods containing likely request-input or dangerous-operation
    # nodes.  This turns the old file-wide CFG dump into a useful pre-query
    # approximation while retaining a safe fallback for unfamiliar languages
    # and the small synthetic slices used by callers/tests.
    interesting = {
        method for method, m_nodes in method_blocks.items()
        if any(_SECURITY_RE.search(str(n.get("code", ""))) for n in m_nodes)
    }
    def _is_global_method(method: str) -> bool:
        return method in {"<global>", "<module>"} or method.endswith(":<global>")

    has_user_methods = any(not _is_global_method(method) for method in method_blocks)

    out: List[str] = []
    for method, m_nodes in method_blocks.items():
        is_global = _is_global_method(method)
        if interesting and not (method in interesting or (is_global and not has_user_methods)):
            # Keep a global block only when the file has no named method.  In
            # Python/JavaScript this removes module imports/decorators; in a
            # PHP file whose vulnerable code is global, the no-user-method
            # fallback preserves it.
            continue
        if is_global or _is_placeholder(m_nodes):
            continue
        func_name = method.split(":")[-1] if ":" in method else method
        params = sorted({
            n["code"] for n in m_nodes if n.get("label") == "METHOD_PARAMETER_IN" and n.get("code")
        })
        out.append(f"function {func_name}({', '.join(params) or '...'}) {{")
        out.extend(_format_body(m_nodes, id_to_node, incoming))
        out.append("}\n")

    global_nodes = [
        n for method, ns in method_blocks.items()
        if _is_global_method(method) and (
            not interesting or not has_user_methods or method in interesting
        )
        for n in ns
    ]
    out.extend(_format_body(global_nodes, id_to_node, incoming))
    return "\n".join(out).strip()
