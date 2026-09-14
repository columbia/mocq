"""Unit tests for trace-style feedback parsing (SARIF paths + probe rewrite). No CodeQL run needed."""

from pathlib import Path

from agent.engines.codeql import CodeQLEngine, _rewrite_as_frontier_probe, _rewrite_as_probe

FIXTURE = Path(__file__).parent / "fixtures" / "sqli_python.sarif"


def test_paths_from_sarif_fixture():
    engine = CodeQLEngine(tool_command="codeql", search_path="")
    text = FIXTURE.read_text()
    paths = engine._paths_from_sarif(text, Path("/does/not/matter"))
    # the fixture was generated from the CWE-089 python seed on the sample corpus
    assert set(paths) == {
        "vuln/flask_sqli_concat.py",
        "vuln/flask_sqli_fstring.py",
        "vuln/flask_sqli_percent.py",
    }
    for f, flows in paths.items():
        assert flows and flows[0], f
        first, last = flows[0][0], flows[0][-1]
        assert first.file == f and last.file == f
        assert first.line >= 1 and last.line >= first.line


def test_paths_from_sarif_empty():
    engine = CodeQLEngine(tool_command="codeql", search_path="")
    assert engine._paths_from_sarif("", Path("/x")) == {}
    assert engine._paths_from_sarif('{"runs": []}', Path("/x")) == {}


_Q = """/**
 * @name t
 * @kind path-problem
 * @problem.severity error
 * @id py/t
 */
import python
import semmle.python.dataflow.new.TaintTracking
predicate mocqSource(DataFlow::Node n) { none() }
predicate mocqSink(DataFlow::Node n) { none() }
module F = TaintTracking::Global<C>;
import F::PathGraph
from F::PathNode a, F::PathNode b
where F::flowPath(a, b)
select b.getNode(), a, b, "m"
"""


def test_rewrite_as_probe_shape():
    probe = _rewrite_as_probe(_Q, "mocqSource")
    assert probe is not None
    assert "@kind problem" in probe and "path-problem" not in probe
    assert "@id py/t-probe-src" in probe
    assert "PathGraph" not in probe          # stripped so it stays a single-clause problem query
    assert probe.rstrip().endswith('select n, "source"')
    assert "where mocqSource(n)" in probe


def test_rewrite_as_probe_missing_predicate():
    assert _rewrite_as_probe(_Q, "mocqBarrier") is None


def test_rewrite_as_probe_no_trailing_select():
    assert _rewrite_as_probe("import python\npredicate mocqSource(X n){none()}\n", "mocqSource") is None


def test_rewrite_as_frontier_probe_shape():
    probe = _rewrite_as_frontier_probe(_Q)
    assert probe is not None
    assert "@kind problem" in probe and "path-problem" not in probe
    assert "@id py/t-probe-frontier" in probe
    assert "PathGraph" not in probe
    assert "module MocqFrontierCfg implements DataFlow::ConfigSig" in probe
    assert "isSource(DataFlow::Node n) { mocqSource(n) }" in probe
    # bounded, not a true any() sink (see the module docstring for why)
    assert "isSink(DataFlow::Node n) { any() }" not in probe
    assert "n.getLocation().getFile() = src.getLocation().getFile()" in probe
    assert "module MocqFrontierFlow = TaintTracking::Global<MocqFrontierCfg>" in probe
    assert probe.rstrip().endswith('select n, "frontier"')


def test_rewrite_as_frontier_probe_missing_source_predicate():
    no_source = _Q.replace("predicate mocqSource(DataFlow::Node n) { none() }", "")
    assert _rewrite_as_frontier_probe(no_source) is None


def test_rewrite_as_frontier_probe_no_trailing_select():
    assert _rewrite_as_frontier_probe(
        "import python\npredicate mocqSource(X n){none()}\n"
    ) is None
