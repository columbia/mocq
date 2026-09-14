"""
Full-loop integration test with a fake engine and a scripted fake session_runner —
no live LLM CLI or real CodeQL/Joern invoked. Exercises phase 1 (per-example, with the
detection-plan and slicing stubs wired in), phase 2 (merge), phase 2.5 (generalize),
and phase 3 (FP-elimination), and checks the resulting summary.json shape.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import agent.synth.analysis as analysis
from agent.engines.base import QueryEngine, DbResult, ValidateResult
from agent.spec import RunSpec, SynthAgentConfig, VulnSpec
from agent.synth.loop import synthesize

_FLAGGED_RE = re.compile(r"#\s*FLAGGED:\s*(.*)")


def _write_flagged(path: Path, flagged: List[str], extra: str = "") -> None:
    body = f"# FLAGGED: {'|'.join(flagged)}\n{extra}"
    path.write_text(body, encoding="utf-8")


class FakeEngine(QueryEngine):
    name = "fake"
    query_extension = ".ql"

    def build_database(self, source_root: Path, language: str, db_path: Path) -> DbResult:
        db_path.mkdir(parents=True, exist_ok=True)
        return DbResult(ok=True, db_path=db_path)

    def validate_query(self, query_path: Path) -> ValidateResult:
        return ValidateResult(ok=True)

    def run_query(self, query_path: Path, db_path: Path, source_root: Path) -> Set[str]:
        text = Path(query_path).read_text(encoding="utf-8")
        m = _FLAGGED_RE.search(text)
        if not m or not m.group(1).strip():
            return set()
        return {p.strip() for p in m.group(1).split("|") if p.strip()}

    def prepare_workspace(self, work_dir: Path, language: str) -> Optional[str]:
        return None

    def slice_file(self, db_path: Path, rel_path: str, timeout: int = 90) -> Optional[dict]:
        if rel_path != "vuln/v1.py":
            return None
        return {
            "nodes": [{"id": 1, "label": "CALL", "code": "sink(x)", "name": "sink",
                       "parentMethod": "<global>", "lineNumber": 1}],
            "edges": [],
        }


def _fake_session_runner(*, cmd, stdin_text, cwd, env, log_path, timeout, provider):
    wip = Path(cwd) / "wip.ql"
    prompt = stdin_text

    if "## The example to catch" in prompt:
        m = re.search(r"## The example to catch\n`([^`]+)`", prompt)
        ex_path = m.group(1)
        # perfect per-example query: flags only its own example, with a literal that only
        # appears in v1.py's source (to trigger the overfit/generalize phase later).
        _write_flagged(wip, [ex_path], extra='// x.nameExact("secretToken123")\n')
    elif "# CodeQL Query Merge" in prompt:
        # merge: union of the per-example queries, plus a spurious FP on safe1.py
        _write_flagged(wip, ["vuln/v1.py", "vuln/v2.py", "safe/safe1.py"],
                       extra='// x.nameExact("secretToken123")\n')
    elif "generalize for recall" in prompt:
        # keep the same detections, drop the hardcoded literal
        _write_flagged(wip, ["vuln/v1.py", "vuln/v2.py", "safe/safe1.py"])
    elif "false-positive elimination" in prompt:
        # drop the false positive, keep both real detections
        _write_flagged(wip, ["vuln/v1.py", "vuln/v2.py"])
    else:
        raise AssertionError(f"fake session runner got an unrecognized prompt:\n{prompt[:300]}")


def _make_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "vuln").mkdir(parents=True)
    (root / "safe").mkdir(parents=True)
    (root / "vuln" / "v1.py").write_text("secretToken123 = query(request.args['x'])\n")
    (root / "vuln" / "v2.py").write_text("os.system(request.args['y'])\n")
    (root / "safe" / "safe1.py").write_text("print('hello')\n")

    manifest = {
        "cwe": "CWE-089", "language": "python", "source_root": "corpus",
        "files": [
            {"path": "vuln/v1.py", "label": "vulnerable"},
            {"path": "vuln/v2.py", "label": "vulnerable"},
            {"path": "safe/safe1.py", "label": "safe"},
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_full_loop_all_phases(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "run_text_completion", lambda **kw: "PLAN-STUB")

    manifest_path = _make_corpus(tmp_path)
    seed_path = tmp_path / "seed.ql"
    seed_path.write_text("# FLAGGED: \n", encoding="utf-8")  # seed baseline: flags nothing

    spec = RunSpec(
        vuln=VulnSpec(id="CWE-089", name="SQL Injection", description="test", languages=["python"]),
        engine="fake",
        seed_query={"fake": {"python": str(seed_path)}},
        benchmark={"python": str(manifest_path)},
        synth_agent=SynthAgentConfig(
            model_provider="claude_cli", prompts_dir="agent/prompts",
            max_per_example_attempts=1, max_fp_refine_rounds=2,
        ),
        config_path=str(tmp_path / "CWE-089_test.json"),
    )

    summary = synthesize(
        spec, FakeEngine(), languages=["python"], out_dir=str(tmp_path / "runs"),
        session_runner=_fake_session_runner,
    )

    lang = summary["languages"]["python"]

    # phase 1: both examples accepted on the first attempt, with the plan + slice wired in
    assert set(lang["accepted_examples"]) == {"vuln/v1.py", "vuln/v2.py"}
    v1_rec = lang["per_example"]["vuln/v1.py"]
    v2_rec = lang["per_example"]["vuln/v2.py"]
    assert v1_rec["accepted"] and v2_rec["accepted"]
    assert v1_rec["detection_plan_used"] and v2_rec["detection_plan_used"]
    assert v1_rec["slice_used"] is True  # slice_file returns data for v1.py only
    assert v2_rec["slice_used"] is False

    run_dir = Path(summary["run_dir"])
    assert (run_dir / "python" / "examples" / "vuln__v1.py" / "plan.txt").read_text().strip() == "PLAN-STUB"
    assert (run_dir / "python" / "examples" / "vuln__v1.py" / "slice.json").is_file()
    assert not (run_dir / "python" / "examples" / "vuln__v2.py" / "slice.json").exists()

    # phase 2: merge accepted, but introduces a false positive on safe1.py
    assert lang["merge"]["status"] == "ok"

    # phase 2.5: generalize fired (the merged query had a literal hardcoded to v1.py only)
    # and was accepted (still retrieves both real examples, no new false positives)
    assert lang["generalize"]["status"] == "accepted"
    assert lang["generalize"]["signals"]

    # phase 3: false positive on safe1.py eliminated, no detection lost
    assert lang["fp_refine"], "expected at least one FP-elimination round"
    assert lang["fp_refine"][-1]["status"] == "accepted"

    # final winner retrieves exactly the two vulnerable files and nothing safe
    assert lang["final_metrics"]["fp"] == 0
    assert lang["final_metrics"]["fn"] == 0
    assert lang["winner"] in ("fp_refine_1", "fp_refine_2")

    assert (run_dir / "summary.json").is_file()
