"""Prompt builders for the synthesis loop. Templates live in agent/prompts/*.txt."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ..engines.base import FlowPath, ProbeResult
from ..spec import RunSpec

_LANG_TAG = {"python": "python", "javascript": "javascript", "java": "java", "php": "php"}


def _load(name: str, prompts_dir: str) -> str:
    p = Path(prompts_dir) / (name if name.endswith(".txt") else f"{name}.txt")
    return p.read_text(encoding="utf-8")


def _fill(template: str, mapping: dict) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


# --------------------------------------------------------------------------- #
# feedback formatting  (trace-style, MoCQ §3.2.2)
# --------------------------------------------------------------------------- #

def render_path(path: FlowPath) -> str:
    parts = []
    for i, step in enumerate(path):
        tag = "source" if i == 0 else ("sink" if i == len(path) - 1 else f"step {i}")
        note = f" — {step.note}" if step.note else ""
        parts.append(f"    [{tag}] {step.file}:{step.line}{note}")
    return "\n".join(parts)


def render_paths_by_file(paths: Dict[str, List[FlowPath]], limit_per_file: int = 3) -> str:
    if not paths:
        return "(no paths reported)"
    blocks = []
    for f in sorted(paths):
        flows = paths[f][:limit_per_file]
        body = "\n\n".join(render_path(p) for p in flows)
        extra = len(paths[f]) - len(flows)
        if extra > 0:
            body += f"\n    ... and {extra} more path(s)"
        blocks.append(f"- {f}\n{body}")
    return "\n".join(blocks)


def render_example_feedback(retrieved: bool, probe: Optional[ProbeResult],
                            paths: Optional[List[FlowPath]]) -> str:
    if retrieved:
        got = "\n".join(render_path(p) for p in (paths or [])) or "    (no path detail)"
        return f"The query already flags this file via:\n{got}\nTighten it only if it also flags safe code."
    if probe is None or probe.unavailable:
        note = probe.note if probe else "no decomposition available"
        return f"The query does NOT flag this file, and it could not be decomposed ({note})."
    kind = probe.classify()
    src = ", ".join(str(x) for x in probe.source_lines) or "none"
    snk = ", ".join(str(x) for x in probe.sink_lines) or "none"
    advice = {
        "no_source_no_sink": "Neither your source nor your sink matched — rebuild both from the harness.",
        "no_source": "Your sink matched but no source did — widen `mocqSource` (framework request input).",
        "no_sink": "Your source matched but no sink did — widen `mocqSink` (the dangerous call in this file).",
        "source_and_sink_but_no_flow": (
            "Source and sink both matched but no taint path connects them — add the missing taint step "
            "(a through-field / through-return / through-call step) or relax an over-eager barrier."
        ),
    }.get(kind, "")
    lines = [
        "The query does NOT flag this file.",
        f"  mocqSource matched at line(s): {src}",
        f"  mocqSink matched at line(s): {snk}",
    ]
    if probe.frontier_lines:
        fr = ", ".join(str(x) for x in probe.frontier_lines)
        lines.append(f"  taint from mocqSource reached line(s): {fr}  <- propagation stalled here")
    if probe.frontier_path:
        lines.append("  stalled path:")
        lines.append(render_path(probe.frontier_path))
    if probe.trace:
        lines.append("  Joern runtime trace (engine-observable block state):")
        for event in probe.trace[:30]:
            code = f" -> {event['code']}" if event.get("code") else ""
            lines.append(
                f"    [{event.get('block', 'unknown')}] "
                f"{event.get('line', -1)}{code}"
            )
        if len(probe.trace) > 30:
            lines.append(f"    ... and {len(probe.trace) - 30} more event(s)")
    lines.append(f"  diagnosis: {kind} — {advice}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# prompt builders
# --------------------------------------------------------------------------- #

def build_perexample_prompt(
    spec: RunSpec,
    language: str,
    *,
    example_path: str,
    example_code: str,
    seed_query_text: str,
    wip_path: str,
    harness: str,
    feedback: str,
    detection_plan: str = "",
    slice_text: str = "",
) -> str:
    template = _load("synth_perexample", spec.synth_agent.prompts_dir)
    return _fill(template, {
        "VULN_ID": spec.vuln.id,
        "VULN_NAME": spec.vuln.name,
        "VULN_DESC": spec.vuln.description,
        "LANGUAGE": language,
        "LANG_TAG": _LANG_TAG.get(language, ""),
        "EXAMPLE_PATH": example_path,
        "EXAMPLE_CODE": example_code.strip()[:6000],
        "SEED_QUERY": seed_query_text.strip(),
        "WIP_PATH": wip_path,
        "HARNESS": harness or "",
        "FEEDBACK": feedback or "(first attempt)",
        "DETECTION_PLAN": detection_plan.strip() or "(none)",
        "SLICE": slice_text.strip() or "(none — using raw file only)",
    })


def build_merge_prompt(
    spec: RunSpec,
    language: str,
    *,
    queries: List[str],
    wip_path: str,
    harness: str,
) -> str:
    template = _load("synth_merge", spec.synth_agent.prompts_dir)
    blocks = "\n\n".join(
        f"### query {i + 1}\n```ql\n{q.strip()}\n```" for i, q in enumerate(queries)
    )
    return _fill(template, {
        "VULN_ID": spec.vuln.id,
        "VULN_NAME": spec.vuln.name,
        "LANGUAGE": language,
        "N_QUERIES": len(queries),
        "QUERIES": blocks,
        "WIP_PATH": wip_path,
        "HARNESS": harness or "",
    })


def build_fp_refine_prompt(
    spec: RunSpec,
    language: str,
    *,
    current_query_text: str,
    fp_paths: Dict[str, List[FlowPath]],
    wip_path: str,
    harness: str,
) -> str:
    template = _load("synth_refine_fp", spec.synth_agent.prompts_dir)
    return _fill(template, {
        "VULN_ID": spec.vuln.id,
        "VULN_NAME": spec.vuln.name,
        "LANGUAGE": language,
        "FALSE_POSITIVE_PATHS": render_paths_by_file(fp_paths),
        "CURRENT_QUERY": current_query_text.strip(),
        "WIP_PATH": wip_path,
        "HARNESS": harness or "",
    })


def render_overfit_signals(signals: List[str]) -> str:
    if not signals:
        return "(none)"
    return "\n".join(f"- {s}" for s in signals)


def build_generalize_prompt(
    spec: RunSpec,
    language: str,
    *,
    current_query_text: str,
    signals: List[str],
    wip_path: str,
    harness: str,
) -> str:
    template = _load("synth_generalize", spec.synth_agent.prompts_dir)
    return _fill(template, {
        "VULN_ID": spec.vuln.id,
        "VULN_NAME": spec.vuln.name,
        "LANGUAGE": language,
        "SIGNALS": render_overfit_signals(signals),
        "CURRENT_QUERY": current_query_text.strip(),
        "WIP_PATH": wip_path,
        "HARNESS": harness or "",
    })


def build_fix_compile_prompt(spec: RunSpec, *, wip_path: str, errors: str) -> str:
    template = _load("synth_fix_compile", spec.synth_agent.prompts_dir)
    return _fill(template, {
        "WIP_PATH": wip_path,
        "COMPILE_ERRORS": errors.strip()[:4000] or "(no output captured)",
    })
