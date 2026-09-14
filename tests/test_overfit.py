"""Unit tests for overfit-signal detection (MoCQ §3.3.2 generalization heuristics)."""

from agent.synth.overfit import detect_overfit_signals

_HARNESS_HEADER = "predicate mocqSource(DataFlow::Node n) {\n"


def test_no_signals_on_clean_query():
    query = """
predicate mocqSource(DataFlow::Node n) { n instanceof RemoteFlowSource }
predicate mocqSink(DataFlow::Node n) { n.asExpr() instanceof SqlExecCall }
"""
    assert detect_overfit_signals(query, {"vuln/a.py": "app.route('/x')"}) == []


def test_flags_literal_hardcoded_to_one_example():
    query = """
predicate mocqSink(DataFlow::Node n) {
  n.asExpr().(Call).getFunc().(Name).nameExact("execmyquery")
}
"""
    files = {
        "vuln/login.py": "def execmyquery(): pass",
        "vuln/other.py": "def unrelated(): pass",
    }
    signals = detect_overfit_signals(query, files)
    assert any("execmyquery" in s and "vuln/login.py" in s for s in signals)


def test_does_not_flag_literal_shared_across_examples():
    query = """
predicate mocqSink(DataFlow::Node n) { n.asExpr().(Call).getFunc().name("execute") }
"""
    files = {"vuln/a.py": "execute(sql)", "vuln/b.py": "execute(other)"}
    # "execute" is a stopword anyway, but also appears in >1 file — either reason, no signal
    assert detect_overfit_signals(query, files) == []


def test_flags_exact_line_number_filter():
    query = """
predicate mocqSink(DataFlow::Node n) { n.getLocation().getStartLine() = 1 and n.lineNumber(42) }
"""
    signals = detect_overfit_signals(query, {})
    assert any("lineNumber" in s for s in signals)


def test_flags_long_astparent_chain():
    query = (
        "predicate mocqSource(DataFlow::Node n) { "
        "n.astParent.astParent.astParent.astParent.astParent.isSource() }"
    )
    signals = detect_overfit_signals(query, {})
    assert any("astParent" in s for s in signals)


def test_ignores_short_or_stopword_tokens():
    query = """
predicate mocqSink(DataFlow::Node n) { n.getName().nameExact("get") }
"""
    files = {"vuln/a.py": "def get(): pass"}
    assert detect_overfit_signals(query, files) == []


def test_dedupes_repeated_signals():
    query = """
predicate mocqSink(DataFlow::Node n) {
  n.getFunc().nameExact("mysecret") or n.getFunc().nameExact("mysecret")
}
"""
    files = {"vuln/a.py": "mysecret()"}
    signals = detect_overfit_signals(query, files)
    assert len(signals) == 1
