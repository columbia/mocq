"""
DSL Harness Extraction + Subsetting (MoCQ §3.1.1 / §3.1.2), for both engines.

    python -m agent.dslgen.extract --engine joern [--joern-src ~/mocq/joern] \\
        [--model-provider claude_cli] [--model MODEL]
    python -m agent.dslgen.extract --engine codeql --language python \\
        [--codeql-repo ~/code-plus/codeql-home/codeql-repo] [--model-provider claude_cli]

Two one-shot LLM calls (agent/synth/session.py::run_text_completion — no MCP/tool-use):
  1. turn a regex source-scan (joern_source_scan.py / codeql_source_scan.py) + best-effort-
     fetched public docs (joern_docs_fetch.py / codeql_docs_fetch.py) into a structured API
     catalog + a short skeleton of a well-formed query.
  2. select the compact, high-expressivity subset of that catalog needed for taint-tracking
     vulnerability queries, reporting the resulting API-count reduction.

Run once per (engine[, language]); Joern's result is engine-wide and overwrites
agent/prompts/joern_dsl.txt. CodeQL's DSL differs per language, so `--language` is required
and the result overwrites agent/prompts/dialect_codeql_<language>.txt. Either way the previous
hand-authored version is preserved as `<name>.manual.txt` on first run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import codeql_docs_fetch, codeql_source_scan, joern_docs_fetch, joern_source_scan
from ..synth.session import run_text_completion

_FENCE_RE = re.compile(r"```[^\r\n]*\r?\n(.*?)```", re.DOTALL)


@dataclass
class ExtractedDsl:
    full_catalog: List[dict]
    subset_catalog: List[dict]
    grammar: str
    keep_justification: str

    @property
    def reduction_pct(self) -> float:
        if not self.full_catalog:
            return 0.0
        return 100.0 * (1 - len(self.subset_catalog) / len(self.full_catalog))


def _extract_fences(text: str) -> List[Tuple[str, str]]:
    # The language tag is intentionally not used by the parser: providers vary
    # between `json`, `text`, `ql`, and an empty tag.  Keeping the body-only
    # contract also makes CRLF responses work.
    return [("", body) for body in _FENCE_RE.findall(text)]


def _parse_extract_response(raw: str) -> Tuple[List[dict], str]:
    catalog: Optional[list] = None
    grammar = ""
    for _lang, body in _extract_fences(raw):
        if catalog is None:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                catalog = parsed
                continue
        if catalog is not None and not grammar and body.strip():
            grammar = body.strip()
    if catalog is None:
        raise ValueError("extraction response did not contain a parseable JSON catalog fence")
    return catalog, grammar


def _parse_subset_response(raw: str) -> Tuple[List[str], str]:
    for _lang, body in _extract_fences(raw):
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "keep" in obj:
            return [str(x) for x in obj.get("keep", [])], str(obj.get("justification", ""))
    raise ValueError("subsetting response did not contain a parseable JSON {keep: [...]} fence")


def _render_scan_material(scanned: List[dict], max_chars: int = 600_000) -> str:
    # CodeQL library scans run into the thousands of entries (~250k-500k chars rendered);
    # the cap here is a safety ceiling against a pathological scan, not a real budget — Joern's
    # own scan (~850 entries) sits comfortably under it too.
    lines = []
    for e in scanned:
        suffix = f"  -- {e['doc']}" if e.get("doc") else ""
        lines.append(f"- {e['signature']}{suffix}  ({e['file']})")
    return "\n".join(lines)[:max_chars]


def _scan_and_fetch_docs(
    engine: str, *, joern_src: Optional[Path], codeql_repo: Optional[Path], language: Optional[str],
) -> Tuple[List[dict], str, str]:
    """Return (scanned_entries, docs_text, engine_label)."""
    if engine == "joern":
        if not joern_src:
            raise ValueError("--joern-src is required for --engine joern")
        scanned = joern_source_scan.scan(joern_src)
        docs_text = joern_docs_fetch.fetch()
        return scanned, docs_text, "Joern/CPGQL (Scala traversal steps)"
    if engine == "codeql":
        if not codeql_repo:
            raise ValueError("--codeql-repo is required for --engine codeql")
        if not language:
            raise ValueError("--language is required for --engine codeql (its DSL differs per language)")
        scanned = codeql_source_scan.scan(codeql_repo, language)
        docs_text = codeql_docs_fetch.fetch(language)
        return scanned, docs_text, f"CodeQL/QL for {language} (classes and predicates)"
    raise ValueError(f"unsupported engine {engine!r}")


def run(
    *,
    engine: str,
    joern_src: Optional[Path] = None,
    codeql_repo: Optional[Path] = None,
    language: Optional[str] = None,
    provider: str = "claude_cli",
    model: Optional[str] = None,
    prompts_dir: str = "agent/prompts",
    timeout: int = 900,
) -> ExtractedDsl:
    scanned, docs_text, engine_label = _scan_and_fetch_docs(
        engine, joern_src=joern_src, codeql_repo=codeql_repo, language=language)
    if not scanned:
        raise RuntimeError(f"no candidate declarations found for {engine_label}")

    extract_template = (Path(prompts_dir) / "dslgen_extract.txt").read_text(encoding="utf-8")
    extract_prompt = (
        extract_template
        .replace("{{ENGINE_LABEL}}", engine_label)
        .replace("{{SCAN_COUNT}}", str(len(scanned)))
        .replace("{{SCAN_MATERIAL}}", _render_scan_material(scanned))
        .replace("{{DOCS_MATERIAL}}", (docs_text or "(docs fetch unavailable — source scan only)")[:20000])
    )
    extract_raw = run_text_completion(provider=provider, prompt=extract_prompt, model=model, timeout=timeout)
    full_catalog, grammar = _parse_extract_response(extract_raw)

    subset_template = (Path(prompts_dir) / "dslgen_subset.txt").read_text(encoding="utf-8")
    subset_prompt = (
        subset_template
        .replace("{{ENGINE_LABEL}}", engine_label)
        .replace("{{FULL_COUNT}}", str(len(full_catalog)))
        .replace("{{FULL_CATALOG_JSON}}", json.dumps(full_catalog, indent=2)[:600_000])
    )
    subset_raw = run_text_completion(provider=provider, prompt=subset_prompt, model=model, timeout=timeout)
    keep_names, justification = _parse_subset_response(subset_raw)
    keep_set = set(keep_names)
    # The extraction prompt asks for `step`, but a source-oriented CodeQL
    # catalog naturally calls the same field `name`.  Accept both forms so a
    # harmless model naming variation does not disable subsetting.
    subset_catalog = [
        e for e in full_catalog if str(e.get("step", e.get("name", ""))) in keep_set
    ] or full_catalog

    return ExtractedDsl(
        full_catalog=full_catalog, subset_catalog=subset_catalog,
        grammar=grammar, keep_justification=justification,
    )


def render_harness(
    extracted: ExtractedDsl, *, title: str, manual_ref: str, generated_by: str = "agent.dslgen.extract",
) -> str:
    lines = [
        f"## {title} — auto-generated by {generated_by}",
        f"## {len(extracted.subset_catalog)}/{len(extracted.full_catalog)} constructs kept "
        f"({extracted.reduction_pct:.0f}% reduction). See {manual_ref} for the hand-authored "
        "reference.",
        "",
        "### Query shape",
        "```",
        extracted.grammar.strip(),
        "```",
        "",
        "### Subset catalog",
    ]
    by_cat: Dict[str, List[dict]] = {}
    for e in extracted.subset_catalog:
        by_cat.setdefault(e.get("category", "other"), []).append(e)
    for cat in sorted(by_cat):
        lines.append(f"\n#### {cat}")
        for e in sorted(by_cat[cat], key=lambda e: e.get("step", "")):
            desc = f" — {e['description']}" if e.get("description") else ""
            step = e.get("step", e.get("name", ""))
            lines.append(f"- `{e.get('signature', step)}`{desc}")
    return "\n".join(lines) + "\n"


def main() -> None:
    try:
        from ..config import _load_dotenv
        _load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default="joern", choices=["joern", "codeql"])
    ap.add_argument("--joern-src", default=os.environ.get("JOERN_SRC", ""),
                    help="Joern source checkout (default: $JOERN_SRC) — --engine joern")
    ap.add_argument("--codeql-repo", default=os.environ.get("CODEQL_SEARCH_PATH", ""),
                    help="CodeQL standard-library checkout (default: $CODEQL_SEARCH_PATH) — --engine codeql")
    ap.add_argument("--language", help="python | javascript | java — required for --engine codeql")
    ap.add_argument("--model-provider", dest="model_provider", default="claude_cli")
    ap.add_argument("--model")
    ap.add_argument("--prompts-dir", default="agent/prompts")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    if args.engine == "joern" and not args.joern_src:
        ap.error("--joern-src is required for --engine joern (or set JOERN_SRC in .env)")
    if args.engine == "codeql":
        if not args.codeql_repo:
            ap.error("--codeql-repo is required for --engine codeql (or set CODEQL_SEARCH_PATH in .env)")
        if not args.language:
            ap.error("--language is required for --engine codeql (its DSL harness differs per language)")

    extracted = run(
        engine=args.engine,
        joern_src=Path(args.joern_src) if args.joern_src else None,
        codeql_repo=Path(args.codeql_repo) if args.codeql_repo else None,
        language=args.language,
        provider=args.model_provider, model=args.model,
        prompts_dir=args.prompts_dir, timeout=args.timeout,
    )

    print(f"full catalog : {len(extracted.full_catalog)} constructs")
    print(f"subset       : {len(extracted.subset_catalog)} constructs "
          f"({extracted.reduction_pct:.1f}% reduction)")
    print(f"justification: {extracted.keep_justification}")

    prompts_dir = Path(args.prompts_dir)
    if args.engine == "joern":
        dsl_path, manual_path = prompts_dir / "joern_dsl.txt", prompts_dir / "joern_dsl.manual.txt"
        title = "CPGQL DSL reference (Joern, Scala)"
    else:
        dsl_path = prompts_dir / f"dialect_codeql_{args.language}.txt"
        manual_path = prompts_dir / f"dialect_codeql_{args.language}.manual.txt"
        title = f"QL DSL reference (CodeQL, {args.language})"

    if dsl_path.is_file() and not manual_path.exists():
        manual_path.write_text(dsl_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"preserved hand-authored harness at {manual_path}")

    dsl_path.write_text(render_harness(extracted, title=title, manual_ref=manual_path.name), encoding="utf-8")
    print(f"wrote {dsl_path}")


if __name__ == "__main__":
    main()
