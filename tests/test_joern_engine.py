"""Unit tests for JoernEngine output parsing / contract checks. No joern server needed."""

from pathlib import Path

from agent.engines.joern import _error_region, _extract_json, _has_def, _relpath


def test_extract_json_list():
    out = 'val res0: String = "[\\"a.php\\",\\"pkg/b.php\\"]"'
    assert _extract_json(out) == ["a.php", "pkg/b.php"]


def test_extract_json_nested_paths():
    payload = '[[{\\"file\\":\\"a.php\\",\\"line\\":4,\\"code\\":\\"x\\"}]]'
    out = f'some repl noise\nval res1: String = "{payload}"\n'
    data = _extract_json(out)
    assert data == [[{"file": "a.php", "line": 4, "code": "x"}]]


def test_extract_json_picks_last_valid():
    out = 'val a: String = "not json"\nval b: String = "[1,2,3]"'
    assert _extract_json(out) == [1, 2, 3]


def test_extract_json_none():
    assert _extract_json("no strings here") is None
    assert _extract_json('val x: Int = 3') is None


def test_has_def():
    src = "def mocqSource = { cpg.call }\ndef mocqSink = cpg.call.argument\n"
    assert _has_def(src, "mocqSource")
    assert _has_def(src, "mocqSink")
    assert not _has_def(src, "mocqSanitizer")


def test_relpath_absolute_and_relative(tmp_path):
    root = tmp_path
    (root / "vuln").mkdir()
    f = root / "vuln" / "a.php"
    f.write_text("<?php")
    assert _relpath(str(f), root) == "vuln/a.php"
    assert _relpath("./vuln/a.php", root) == "vuln/a.php"
    assert _relpath('"vuln/a.php"', root) == "vuln/a.php"


def test_error_region_finds_marker():
    out = "line1\nline2\nsome -- [E008] Not Found Error here\nvalue x is not a member\nline5"
    region = _error_region(out)
    assert "[E008]" in region and "not a member" in region


def test_probe_result_frontier_in_feedback():
    from agent.engines.base import ProbeResult
    from agent.synth.prompts import render_example_feedback

    pr = ProbeResult(source_lines=[3], sink_lines=[9], frontier_lines=[3, 5])
    fb = render_example_feedback(retrieved=False, probe=pr, paths=None)
    assert "reached line(s): 3, 5" in fb
    assert "source_and_sink_but_no_flow" in fb
    # frontier defaults empty and is simply omitted
    pr2 = ProbeResult(source_lines=[], sink_lines=[9])
    assert "reached line(s)" not in render_example_feedback(False, pr2, None)


def test_probe_result_runtime_trace_in_feedback():
    from agent.engines.base import ProbeResult
    from agent.synth.prompts import render_example_feedback

    pr = ProbeResult(
        source_lines=[3], sink_lines=[9],
        trace=[{"block": "mocqSource", "line": 3, "code": "req.query.id"}],
    )
    fb = render_example_feedback(retrieved=False, probe=pr, paths=None)
    assert "Joern runtime trace" in fb
    assert "[mocqSource] 3 -> req.query.id" in fb
