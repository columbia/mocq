"""
Vulnerability Analysis (MoCQ paper §3.2.1) — a chain-of-thought decomposition of one
vulnerable example into a short detection plan (dangerous operation, entry point, the
path between them, missing sanitization), computed once before query generation starts
on that example and folded into the per-example prompt. Engine-neutral.
"""

from __future__ import annotations

from pathlib import Path

from ..spec import RunSpec
from .session import run_text_completion

_LANG_TAG = {"python": "python", "javascript": "javascript", "java": "java", "php": "php"}


def build_detection_plan(
    spec: RunSpec,
    language: str,
    *,
    example_path: str,
    example_code: str,
    timeout: int = 300,
) -> str:
    """Return a short plain-text detection plan, or a fallback note if the LLM call fails."""
    template = (Path(spec.synth_agent.prompts_dir) / "synth_vuln_analysis.txt").read_text(
        encoding="utf-8"
    )
    prompt = (
        template
        .replace("{{VULN_ID}}", spec.vuln.id)
        .replace("{{VULN_NAME}}", spec.vuln.name)
        .replace("{{VULN_DESC}}", spec.vuln.description)
        .replace("{{LANGUAGE}}", language)
        .replace("{{LANG_TAG}}", _LANG_TAG.get(language, ""))
        .replace("{{EXAMPLE_PATH}}", example_path)
        .replace("{{EXAMPLE_CODE}}", example_code.strip()[:6000])
    )
    try:
        plan = run_text_completion(
            provider=spec.synth_agent.model_provider,
            prompt=prompt,
            model=spec.synth_agent.model,
            timeout=timeout,
        )
    except RuntimeError as exc:
        return f"(detection plan unavailable: {exc})"
    return plan.strip() or "(empty detection plan)"
