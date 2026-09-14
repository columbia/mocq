"""Unit tests for per-file scoring and manifest loading (no CodeQL needed)."""

import json
from pathlib import Path

import pytest

from agent.benchmark.corpus import load_manifest
from agent.benchmark.runner import Metrics, score_per_file


def test_metrics_math():
    m = Metrics(tp=3, fp=1, fn=1, tn=5)
    assert m.precision == pytest.approx(0.75)
    assert m.recall == pytest.approx(0.75)
    assert m.f1 == pytest.approx(0.75)
    assert m.accuracy == pytest.approx(8 / 10)


def test_metrics_empty():
    m = Metrics()
    assert m.precision == 0.0 and m.recall == 0.0 and m.f1 == 0.0


def _write_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "CWE-089" / "python"
    (root / "vuln").mkdir(parents=True)
    (root / "safe").mkdir(parents=True)
    for n in ("a", "b", "c"):
        (root / "vuln" / f"{n}.py").write_text("# vuln\n")
    for n in ("x", "y"):
        (root / "safe" / f"{n}.py").write_text("# safe\n")
    manifest = {
        "cwe": "CWE-089",
        "language": "python",
        "source_root": ".",
        "files": [
            {"path": "vuln/a.py", "label": "vulnerable"},
            {"path": "vuln/b.py", "label": "vulnerable"},
            {"path": "vuln/c.py", "label": "vulnerable"},
            {"path": "safe/x.py", "label": "safe"},
            {"path": "safe/y.py", "label": "safe"},
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root / "manifest.json"


def test_score_per_file_perfect(tmp_path):
    manifest = load_manifest(_write_corpus(tmp_path))
    res = score_per_file({"vuln/a.py", "vuln/b.py", "vuln/c.py"}, manifest)
    assert res.metrics.as_dict()["f1"] == 1.0
    assert res.missed == [] and res.false_alarms == []


def test_score_per_file_mixed(tmp_path):
    manifest = load_manifest(_write_corpus(tmp_path))
    # catches a,b ; misses c ; false alarm on x
    res = score_per_file({"./vuln/a.py", "vuln/b.py", "safe/x.py"}, manifest)
    m = res.metrics
    assert (m.tp, m.fp, m.fn, m.tn) == (2, 1, 1, 1)
    assert res.missed == ["vuln/c.py"]
    assert res.false_alarms == ["safe/x.py"]


def test_manifest_rejects_missing_file(tmp_path):
    manifest_path = _write_corpus(tmp_path)
    data = json.loads(manifest_path.read_text())
    data["files"].append({"path": "vuln/ghost.py", "label": "vulnerable"})
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(FileNotFoundError):
        load_manifest(manifest_path)


def test_manifest_rejects_bad_label(tmp_path):
    manifest_path = _write_corpus(tmp_path)
    data = json.loads(manifest_path.read_text())
    data["files"][0]["label"] = "maybe"
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_manifest(manifest_path)
