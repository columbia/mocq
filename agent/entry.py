"""
One-phase web-query synthesis entry point (MoCQ-style: per-example -> merge -> FP-elimination).

    python -m agent.entry --config configs/CWE-089_sqli.json \\
        [--languages python,javascript,java] \\
        [--max-per-example-attempts 5] [--max-fp-rounds 3] \\
        [--engine codeql] [--model-provider claude_cli] [--model MODEL] [--out runs]

For each language: generate a query per known-vulnerable file (trace-style feedback), LLM-merge the
accepted ones, then eliminate false positives against the safe set. Best-F1 query per language lands
in ``runs/<config>/final/``.
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .engines import get_engine
from .spec import RunSpec
from .synth import synthesize


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="vulnerability config JSON")
    ap.add_argument("--languages", help="comma-separated subset of the config's vuln.languages")
    ap.add_argument("--max-per-example-attempts", dest="max_per_example_attempts", type=int,
                    help="override synth_agent.max_per_example_attempts")
    ap.add_argument("--max-fp-rounds", dest="max_fp_rounds", type=int,
                    help="override synth_agent.max_fp_refine_rounds")
    ap.add_argument("--max-rounds", dest="max_rounds", type=int,
                    help="deprecated alias for --max-fp-rounds")
    ap.add_argument("--engine", choices=["codeql", "joern"], help="override config engine")
    ap.add_argument("--model", help="override synth_agent.model")
    ap.add_argument("--model-provider", dest="model_provider",
                    help="override synth_agent.model_provider (claude_cli / agy_cli / gemini_cli)")
    ap.add_argument("--max-turns", type=int, help="override synth_agent.cli_max_turns")
    ap.add_argument("--timeout", type=int, help="override synth_agent.cli_timeout (seconds)")
    ap.add_argument("--no-detection-plan", dest="no_detection_plan", action="store_true",
                    help="skip the MoCQ vulnerability-analysis / detection-plan step")
    ap.add_argument("--no-slicing", dest="no_slicing", action="store_true",
                    help="skip per-example program slicing (Joern only)")
    ap.add_argument("--no-generalization", dest="no_generalization", action="store_true",
                    help="skip the overfit-detection / generalization phase")
    ap.add_argument("--out", default="runs", help="output root directory")
    args = ap.parse_args()

    raw = load_config(args.config)
    spec = RunSpec.from_dict(raw, config_path=args.config)

    if args.engine:
        spec.engine = args.engine
    if args.model:
        spec.synth_agent.model = args.model
    if args.model_provider:
        spec.synth_agent.model_provider = args.model_provider
    if args.max_turns:
        spec.synth_agent.cli_max_turns = args.max_turns
    if args.timeout:
        spec.synth_agent.cli_timeout = args.timeout
    if args.max_per_example_attempts:
        spec.synth_agent.max_per_example_attempts = args.max_per_example_attempts
    fp_rounds = args.max_fp_rounds if args.max_fp_rounds is not None else args.max_rounds
    if fp_rounds is not None:
        spec.synth_agent.max_fp_refine_rounds = fp_rounds
    if args.no_detection_plan:
        spec.synth_agent.enable_detection_plan = False
    if args.no_slicing:
        spec.synth_agent.enable_slicing = False
    if args.no_generalization:
        spec.synth_agent.enable_generalization = False

    languages = (
        [l.strip().lower() for l in args.languages.split(",") if l.strip()]
        if args.languages else list(spec.vuln.languages)
    )

    engine = get_engine(
        spec.engine,
        tool_command=spec.synth_agent.tool_command,
        search_path=spec.synth_agent.tool_search_path,
        joern_home=spec.synth_agent.joern_home,
    )

    print(f"config     : {args.config}")
    print(f"vuln       : {spec.vuln.id} {spec.vuln.name}")
    print(f"engine     : {spec.engine}")
    print(f"provider   : {spec.synth_agent.model_provider} / {spec.synth_agent.model or '(default)'}")
    print(f"languages  : {', '.join(languages)}")
    print(f"per-example: {spec.synth_agent.max_per_example_attempts} attempts   "
          f"fp-rounds: {spec.synth_agent.max_fp_refine_rounds}")

    summary = synthesize(spec, engine, languages=languages, out_dir=args.out)

    print("\nfinal metrics:")
    ok = True
    for lang, res in summary["languages"].items():
        fm = res.get("final_metrics")
        base = res.get("baseline_metrics")
        if fm:
            delta = f"  (seed f1={base['f1']:.3f})" if base else ""
            print(f"  {lang:11s} f1={fm['f1']:.3f}  precision={fm['precision']:.3f}  "
                  f"recall={fm['recall']:.3f}  [{res.get('winner')}]{delta}")
        else:
            print(f"  {lang:11s} (no scored query)")
            ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
