"""
MoCQ-style synthesis orchestrator (per-example generate -> merge -> FP-elimination).

Per language:
  Phase 1  for each vulnerable file (an "example"), run up to N agent attempts to
           write a query that flags it; feedback is trace-style (source/sink
           decomposition for misses, the found path for hits).
  Phase 2  LLM-merge the accepted per-example queries into one.
  Phase 3  score the merged query on the whole corpus and run FP-elimination
           rounds against the flagged safe files, showing the agent the exact
           source -> sink path to break.
The best-F1 query among {seed, merged, each FP round} wins. The orchestrator
owns all evaluation; agent sessions only get compile-level tools.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..benchmark.corpus import _norm, load_manifest
from ..benchmark.runner import ScoreResult, build_or_get_db, score_per_file
from ..engines.base import FlowPath, ProbeResult, QueryEngine, ValidateResult
from ..spec import RunSpec
from . import analysis, overfit, prompts, slicing
from .session import (
    build_cli_command,
    extract_last_ql_block,
    register_mcp_servers,
    resolve_executable,
    run_agent_session,
)


# --------------------------------------------------------------------------- #
# session plumbing
# --------------------------------------------------------------------------- #

def _session_env(engine: QueryEngine) -> Dict[str, str]:
    env = os.environ.copy()
    env.update(engine.session_env())
    return env


def _write_gemini_settings(work_dir: Path, engine: QueryEngine) -> None:
    cfg = engine.mcp_server_config()
    if not cfg:
        return
    d = work_dir / ".gemini"
    d.mkdir(exist_ok=True)
    settings_path = d / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            settings = {}
    settings.setdefault("mcpServers", {}).update(cfg.get("mcpServers", {}))
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _setup_language_workspace(
    spec: RunSpec, engine: QueryEngine, work_dir: Path, language: str
) -> Optional[str]:
    work_dir.mkdir(parents=True, exist_ok=True)
    mcp_config_path = engine.prepare_workspace(work_dir, language)
    provider = spec.synth_agent.model_provider
    if provider == "gemini_cli":
        _write_gemini_settings(work_dir, engine)
        return None
    if provider == "agy_cli":
        executable, _ = resolve_executable(provider)
        register_mcp_servers(provider, executable, engine.mcp_server_config())
        return None
    return mcp_config_path


def _run_session(
    spec: RunSpec, engine: QueryEngine, *, prompt: str, work_dir: Path,
    mcp_config_path: Optional[str], log_path: Path, session_runner: Callable,
) -> None:
    provider = spec.synth_agent.model_provider
    executable, _ = resolve_executable(provider)
    env = _session_env(engine)
    cmd, stdin_text = build_cli_command(
        provider=provider, executable=executable, prompt=prompt,
        mcp_config_path=mcp_config_path,
        allowed_tools=",".join(engine.session_allowed_tools()),
        max_turns=spec.synth_agent.cli_max_turns,
        timeout=spec.synth_agent.cli_timeout,
        model=spec.synth_agent.model, env=env,
    )
    session_runner(
        cmd=cmd, stdin_text=stdin_text, cwd=work_dir, env=env,
        log_path=log_path, timeout=spec.synth_agent.cli_timeout, provider=provider,
    )


def _recover_query_from_log(spec: RunSpec, wip: Path, log_path: Path, before_text: str) -> None:
    """CLIs that print a ```ql block instead of using Write (gemini; sometimes agy)."""
    if spec.synth_agent.model_provider not in ("gemini_cli", "agy_cli"):
        return
    if not log_path.exists():
        return
    if wip.read_text(encoding="utf-8").strip() != before_text.strip():
        return
    block = extract_last_ql_block(log_path.read_text(encoding="utf-8"))
    if block:
        wip.write_text(block + "\n", encoding="utf-8")


def _agent_write_query(
    spec: RunSpec, engine: QueryEngine, *, prompt: str, work_dir: Path, wip: Path,
    before_text: str, mcp_config_path: Optional[str], log_path: Path, session_runner: Callable,
) -> ValidateResult:
    """One agent session + at most one compile-fix sub-session. Leaves wip.ql on disk."""
    _run_session(spec, engine, prompt=prompt, work_dir=work_dir,
                 mcp_config_path=mcp_config_path, log_path=log_path, session_runner=session_runner)
    _recover_query_from_log(spec, wip, log_path, before_text)

    vr = engine.validate_query(wip)
    if not vr.ok and not vr.infrastructure_error:
        fix_prompt = prompts.build_fix_compile_prompt(spec, wip_path=str(wip), errors=vr.errors)
        fix_log = log_path.with_name("fix_" + log_path.name)
        (fix_log.with_suffix(".prompt.txt")).write_text(fix_prompt, encoding="utf-8")
        pre_fix = wip.read_text(encoding="utf-8")
        _run_session(spec, engine, prompt=fix_prompt, work_dir=work_dir,
                     mcp_config_path=mcp_config_path, log_path=fix_log, session_runner=session_runner)
        _recover_query_from_log(spec, wip, fix_log, pre_fix)
        vr = engine.validate_query(wip)
    return vr


# --------------------------------------------------------------------------- #
# evaluation helpers  (orchestrator-owned)
# --------------------------------------------------------------------------- #

def _match(rel: str, flagged) -> bool:
    return _norm(rel) in {_norm(x) for x in flagged}


def _lookup(d: Dict[str, list], rel: str) -> list:
    if rel in d:
        return d[rel]
    key = _norm(rel)
    for k, v in d.items():
        if _norm(k) == key:
            return v
    return []


def _probe_to_dict(p: Optional[ProbeResult]) -> Optional[dict]:
    if p is None:
        return None
    return {
        "source_lines": p.source_lines, "sink_lines": p.sink_lines,
        "frontier_lines": p.frontier_lines,
        "frontier_path": [{"file": s.file, "line": s.line, "note": s.note} for s in p.frontier_path],
        "trace": p.trace,
        "unavailable": p.unavailable, "diagnosis": p.classify(), "note": p.note,
    }


def _paths_to_dicts(paths: Optional[List[FlowPath]]) -> Optional[list]:
    if not paths:
        return None
    return [[{"file": s.file, "line": s.line, "note": s.note} for s in p] for p in paths]


def _example_feedback(
    engine: QueryEngine, query_path: Path, db_path: Path, ex_rel: str, source_root: Path,
) -> Tuple[bool, Optional[ProbeResult], Optional[List[FlowPath]]]:
    all_paths: Optional[Dict[str, List[FlowPath]]] = None
    try:
        all_paths = engine.run_query_paths(query_path, db_path, source_root)
    except (RuntimeError, NotImplementedError):
        all_paths = None

    if all_paths is not None:
        retrieved = _match(ex_rel, all_paths.keys())
        if retrieved:
            return True, None, _lookup(all_paths, ex_rel) or None
    else:
        try:
            flagged = engine.run_query(query_path, db_path, source_root)
        except RuntimeError:
            return False, None, None
        if _match(ex_rel, flagged):
            return True, None, None

    try:
        probe = engine.probe_missed(query_path, db_path, source_root, [ex_rel]).get(ex_rel)
    except (RuntimeError, NotImplementedError):
        probe = None
    return False, probe, None


# --------------------------------------------------------------------------- #
# per-language synthesis
# --------------------------------------------------------------------------- #

def _stem(rel: str) -> str:
    return Path(rel).as_posix().strip("/").replace("/", "__") or "example"


def _synthesize_language(
    spec: RunSpec, engine: QueryEngine, language: str, run_dir: Path, *, session_runner: Callable,
) -> dict:
    print(f"\n{'=' * 60}\n{spec.vuln.id} / {language}\n{'=' * 60}")
    ext = engine.query_extension
    lang_dir = run_dir / language
    work_dir = lang_dir
    wip = work_dir / f"wip{ext}"

    seed_path = Path(spec.seed_for(language)).expanduser()
    if not seed_path.is_file():
        raise FileNotFoundError(f"seed query not found for {language}: {seed_path}")
    seed_text = seed_path.read_text(encoding="utf-8")

    mcp_config_path = _setup_language_workspace(spec, engine, work_dir, language)
    harness = engine.dialect_hints(language)
    (work_dir / "harness.txt").write_text(harness, encoding="utf-8")

    manifest = load_manifest(spec.manifest_for(language))
    source_root = manifest.source_root
    print(f"benchmark: {len(manifest.vulnerable)} vulnerable / {len(manifest.safe)} safe")
    db_path = build_or_get_db(manifest, engine)
    engine.open_database(db_path, language)  # no-op for stateless engines; close() in synthesize()

    def score_text(text: str) -> Optional[ScoreResult]:
        wip.write_text(text, encoding="utf-8")
        try:
            return score_per_file(engine.run_query(wip, db_path, source_root), manifest)
        except RuntimeError as exc:
            print(f"   scoring failed: {exc}")
            return None

    candidates: List[Tuple[str, str, ScoreResult]] = []
    try:
        base_score = score_per_file(engine.run_query(seed_path, db_path, source_root), manifest)
        bm = base_score.metrics
        print(f"seed baseline: f1={bm.f1:.3f}  precision={bm.precision:.3f}  recall={bm.recall:.3f}")
        candidates.append(("seed", seed_text, base_score))
    except RuntimeError as exc:
        print(f"seed baseline failed (continuing): {exc}")
        base_score = None

    # ------------------------------------------------------------------ #
    # Phase 1 — per-example queries
    # ------------------------------------------------------------------ #
    print(f"\n--- phase 1: per-example ({len(manifest.vulnerable)} examples) ---")
    per_example: Dict[str, dict] = {}
    accepted: Dict[str, str] = {}
    example_sources: Dict[str, str] = {}
    max_attempts = spec.synth_agent.max_per_example_attempts
    slice_fn = getattr(engine, "slice_file", None)

    for ex in manifest.vulnerable:
        ex_dir = lang_dir / "examples" / _stem(ex)
        ex_dir.mkdir(parents=True, exist_ok=True)
        try:
            ex_code = (source_root / ex).read_text(encoding="utf-8", errors="replace")
        except OSError:
            ex_code = "(source unavailable)"
        example_sources[ex] = ex_code
        rec = {"file": ex, "accepted": False, "attempts": 0, "last_feedback": ""}
        feedback = "(first attempt)"
        wip.write_text(seed_text, encoding="utf-8")
        print(f"  example {ex}")

        # detection plan (MoCQ §3.2.1) — computed once per example, reused across retries
        detection_plan = ""
        if spec.synth_agent.enable_detection_plan:
            detection_plan = analysis.build_detection_plan(
                spec, language, example_path=ex, example_code=ex_code)
            (ex_dir / "plan.txt").write_text(detection_plan, encoding="utf-8")
        rec["detection_plan_used"] = bool(detection_plan)

        # program slice (MoCQ §3.2, Joern only) — same, once per example
        slice_text = ""
        if spec.synth_agent.enable_slicing and slice_fn is not None:
            try:
                slice_json = slice_fn(db_path, ex)
            except Exception as exc:  # noqa: BLE001 - slicing is best-effort, never fatal
                slice_json = None
                print(f"    slice_file failed for {ex}: {exc}")
            if slice_json:
                (ex_dir / "slice.json").write_text(json.dumps(slice_json, indent=2), encoding="utf-8")
                slice_text = slicing.reconstruct(slice_json)
                if slice_text:
                    (ex_dir / "slice.txt").write_text(slice_text, encoding="utf-8")
        rec["slice_used"] = bool(slice_text)

        for attempt in range(1, max_attempts + 1):
            rec["attempts"] = attempt
            rd = ex_dir / f"round_{attempt:02d}"
            rd.mkdir(parents=True, exist_ok=True)
            before = wip.read_text(encoding="utf-8")
            prompt = prompts.build_perexample_prompt(
                spec, language, example_path=ex, example_code=ex_code,
                seed_query_text=seed_text, wip_path=str(wip), harness=harness, feedback=feedback,
                detection_plan=detection_plan, slice_text=slice_text,
            )
            (rd / "prompt.txt").write_text(prompt, encoding="utf-8")
            vr = _agent_write_query(
                spec, engine, prompt=prompt, work_dir=work_dir, wip=wip, before_text=before,
                mcp_config_path=mcp_config_path, log_path=rd / "session.jsonl",
                session_runner=session_runner,
            )
            (rd / f"query{ext}").write_text(wip.read_text(encoding="utf-8"), encoding="utf-8")

            if not vr.ok and not vr.infrastructure_error:
                feedback = f"Your previous query did not compile:\n{vr.errors[:1500]}"
                (rd / "feedback.json").write_text(
                    json.dumps({"compile_error": vr.errors[:2000]}, indent=2), encoding="utf-8")
                print(f"    attempt {attempt}: compile failed")
                continue

            retrieved, probe, paths = _example_feedback(engine, wip, db_path, ex, source_root)
            (rd / "feedback.json").write_text(json.dumps({
                "retrieved": retrieved,
                "probe": _probe_to_dict(probe),
                "paths": _paths_to_dicts(paths),
            }, indent=2), encoding="utf-8")

            if retrieved:
                rec["accepted"] = True
                accepted[ex] = wip.read_text(encoding="utf-8")
                (ex_dir / f"accepted{ext}").write_text(accepted[ex], encoding="utf-8")
                print(f"    attempt {attempt}: accepted")
                break
            feedback = prompts.render_example_feedback(retrieved, probe, paths)
            print(f"    attempt {attempt}: not retrieved ({probe.classify() if probe else 'n/a'})")

        rec["last_feedback"] = feedback
        per_example[ex] = rec

    print(f"  accepted {len(accepted)}/{len(manifest.vulnerable)} examples")

    # ------------------------------------------------------------------ #
    # Phase 2 — merge
    # ------------------------------------------------------------------ #
    print("\n--- phase 2: merge ---")
    merge_rec: dict = {"status": "skipped"}
    if len(accepted) >= 2:
        md = lang_dir / "merge"
        md.mkdir(parents=True, exist_ok=True)
        first = next(iter(accepted.values()))
        wip.write_text(first, encoding="utf-8")
        prompt = prompts.build_merge_prompt(
            spec, language, queries=list(accepted.values()), wip_path=str(wip), harness=harness)
        (md / "prompt.txt").write_text(prompt, encoding="utf-8")
        vr = _agent_write_query(
            spec, engine, prompt=prompt, work_dir=work_dir, wip=wip, before_text=first,
            mcp_config_path=mcp_config_path, log_path=md / "session.jsonl", session_runner=session_runner,
        )
        (md / f"merged{ext}").write_text(wip.read_text(encoding="utf-8"), encoding="utf-8")
        if not vr.ok and not vr.infrastructure_error:
            merge_rec = {"status": "compile_failed", "errors": vr.errors[:1500]}
            merged_text = first
        else:
            try:
                flagged = engine.run_query(wip, db_path, source_root)
                missing = [e for e in accepted if not _match(e, flagged)]
            except RuntimeError as exc:
                missing = list(accepted)
                merge_rec["error"] = str(exc)[:500]
            if missing:
                merge_rec = {"status": "regressed", "missing_examples": missing}
                merged_text = first
            else:
                merge_rec = {"status": "ok"}
                merged_text = wip.read_text(encoding="utf-8")
        (md / "validation.json").write_text(json.dumps(merge_rec, indent=2), encoding="utf-8")
    elif accepted:
        merged_text = next(iter(accepted.values()))
        merge_rec = {"status": "single"}
    else:
        merged_text = seed_text
        merge_rec = {"status": "no_accepted_examples"}
    print(f"  merge: {merge_rec['status']}")

    merged_score = score_text(merged_text)
    if merged_score is not None:
        candidates.append(("merged", merged_text, merged_score))
        mm = merged_score.metrics
        print(f"  merged score: f1={mm.f1:.3f}  p={mm.precision:.3f}  r={mm.recall:.3f}  "
              f"fp={mm.fp} fn={mm.fn}")

    # ------------------------------------------------------------------ #
    # Phase 2.5 — generalize for recall (MoCQ §3.3.2)
    # ------------------------------------------------------------------ #
    print("\n--- phase 2.5: generalize ---")
    generalize_rec: dict = {"status": "skipped"}
    cur_text, cur_score = merged_text, merged_score
    if spec.synth_agent.enable_generalization and accepted and merged_score is not None:
        example_files = {e: example_sources.get(e, "") for e in accepted}
        signals = overfit.detect_overfit_signals(merged_text, example_files)
        if not signals:
            generalize_rec = {"status": "no_signals"}
        else:
            gd = lang_dir / "generalize"
            gd.mkdir(parents=True, exist_ok=True)
            wip.write_text(merged_text, encoding="utf-8")
            prompt = prompts.build_generalize_prompt(
                spec, language, current_query_text=merged_text, signals=signals,
                wip_path=str(wip), harness=harness,
            )
            (gd / "prompt.txt").write_text(prompt, encoding="utf-8")
            vr = _agent_write_query(
                spec, engine, prompt=prompt, work_dir=work_dir, wip=wip, before_text=merged_text,
                mcp_config_path=mcp_config_path, log_path=gd / "session.jsonl", session_runner=session_runner,
            )
            (gd / f"generalized{ext}").write_text(wip.read_text(encoding="utf-8"), encoding="utf-8")
            if not vr.ok and not vr.infrastructure_error:
                generalize_rec = {"status": "compile_failed", "errors": vr.errors[:1500], "signals": signals}
            else:
                new_text = wip.read_text(encoding="utf-8")
                new_score = score_text(new_text)
                if new_score is None:
                    generalize_rec = {"status": "eval_failed", "signals": signals}
                else:
                    try:
                        flagged = engine.run_query(wip, db_path, source_root)
                        missing = [e for e in accepted if not _match(e, flagged)]
                    except RuntimeError:
                        missing = list(accepted)
                    if missing or len(new_score.false_alarms) > len(merged_score.false_alarms):
                        generalize_rec = {
                            "status": "rejected_regression", "signals": signals, "missing_examples": missing,
                        }
                    else:
                        generalize_rec = {"status": "accepted", "signals": signals, **new_score.as_dict()}
                        candidates.append(("generalized", new_text, new_score))
                        cur_text, cur_score = new_text, new_score
            (gd / "validation.json").write_text(json.dumps(generalize_rec, indent=2), encoding="utf-8")
    print(f"  generalize: {generalize_rec['status']}")

    # ------------------------------------------------------------------ #
    # Phase 3 — FP elimination
    # ------------------------------------------------------------------ #
    print("\n--- phase 3: FP elimination ---")
    fp_trace: List[dict] = []
    for rnd in range(1, spec.synth_agent.max_fp_refine_rounds + 1):
        if cur_score is None or not cur_score.false_alarms:
            print("  no false positives — done" if cur_score else "  no score — skipping")
            break
        fd = lang_dir / "fp_refine" / f"round_{rnd:02d}"
        fd.mkdir(parents=True, exist_ok=True)
        wip.write_text(cur_text, encoding="utf-8")
        try:
            all_paths = engine.run_query_paths(wip, db_path, source_root)
        except (RuntimeError, NotImplementedError):
            all_paths = {}
        fp_paths = {f: _lookup(all_paths, f) for f in cur_score.false_alarms}
        prompt = prompts.build_fp_refine_prompt(
            spec, language, current_query_text=cur_text, fp_paths=fp_paths,
            wip_path=str(wip), harness=harness)
        (fd / "prompt.txt").write_text(prompt, encoding="utf-8")
        vr = _agent_write_query(
            spec, engine, prompt=prompt, work_dir=work_dir, wip=wip, before_text=cur_text,
            mcp_config_path=mcp_config_path, log_path=fd / "session.jsonl", session_runner=session_runner,
        )
        (fd / f"query{ext}").write_text(wip.read_text(encoding="utf-8"), encoding="utf-8")
        rec: dict = {"round": rnd}
        if not vr.ok and not vr.infrastructure_error:
            rec["status"] = "compile_failed"
            fp_trace.append(rec)
            break
        new_score = score_text(wip.read_text(encoding="utf-8"))
        if new_score is None:
            rec["status"] = "eval_failed"
            fp_trace.append(rec)
            break
        rec.update(new_score.as_dict())
        if len(new_score.missed) > len(cur_score.missed):
            rec["status"] = "rejected_regression"
            fp_trace.append(rec)
            print(f"  round {rnd}: rejected (lost {len(new_score.missed) - len(cur_score.missed)} detection)")
            break
        rec["status"] = "accepted"
        fp_trace.append(rec)
        cur_text = wip.read_text(encoding="utf-8")
        cur_score = new_score
        candidates.append((f"fp_refine_{rnd}", cur_text, cur_score))
        nm = new_score.metrics
        print(f"  round {rnd}: accepted  f1={nm.f1:.3f}  fp={nm.fp}  fn={nm.fn}")
        if not new_score.false_alarms:
            break

    # ------------------------------------------------------------------ #
    # pick winner (best F1; safety net = seed)
    # ------------------------------------------------------------------ #
    scored = [(lbl, q, s) for lbl, q, s in candidates if s is not None]
    if scored:
        # best F1; on a tie prefer the later (more "learned") candidate:
        # seed < merged < fp_refine_1 < fp_refine_2 ...
        winner_lbl, winner_q, winner_s = max(
            ((lbl, q, s, i) for i, (lbl, q, s) in enumerate(scored)),
            key=lambda t: (t[2].metrics.f1, t[3]),
        )[:3]
    else:
        winner_lbl, winner_q, winner_s = "seed", seed_text, None

    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / f"{spec.vuln.id}-{language}{ext}"
    final_path.write_text(winner_q, encoding="utf-8")
    wip.write_text(winner_q, encoding="utf-8")

    if winner_s:
        wm = winner_s.metrics
        print(f"\nwinner: {winner_lbl}  f1={wm.f1:.3f}  precision={wm.precision:.3f}  recall={wm.recall:.3f}")

    return {
        "language": language,
        "seed_query": str(seed_path),
        "final_query": str(final_path),
        "winner": winner_lbl,
        "baseline_metrics": base_score.metrics.as_dict() if base_score else None,
        "per_example": per_example,
        "accepted_examples": list(accepted),
        "merge": merge_rec,
        "generalize": generalize_rec,
        "fp_refine": fp_trace,
        "final_metrics": winner_s.metrics.as_dict() if winner_s else None,
        "final_breakdown": (
            {"missed": winner_s.missed, "false_alarms": winner_s.false_alarms} if winner_s else None
        ),
    }


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #

def synthesize(
    spec: RunSpec,
    engine: QueryEngine,
    *,
    languages: Optional[List[str]] = None,
    out_dir: str = "runs",
    session_runner: Callable = run_agent_session,
) -> dict:
    languages = languages or list(spec.vuln.languages)
    unknown = [l for l in languages if l not in spec.vuln.languages]
    if unknown:
        raise ValueError(f"languages {unknown} not in config vuln.languages {spec.vuln.languages}")

    run_dir = Path(out_dir).resolve() / spec.stem
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "config": spec.config_path,
        "engine": spec.engine,
        "vuln": {"id": spec.vuln.id, "name": spec.vuln.name},
        "run_dir": str(run_dir),
        "max_per_example_attempts": spec.synth_agent.max_per_example_attempts,
        "max_fp_refine_rounds": spec.synth_agent.max_fp_refine_rounds,
        "languages": {},
    }

    for language in languages:
        try:
            summary["languages"][language] = _synthesize_language(
                spec, engine, language, run_dir, session_runner=session_runner,
            )
        finally:
            engine.close()  # tear down a per-language server (no-op for stateless engines)
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nsummary: {run_dir / 'summary.json'}")
    return summary
