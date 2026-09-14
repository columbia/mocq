"""
Unit tests for the DSL harness extraction pipeline (agent/dslgen/) — the source scan and
the LLM-response parsing/rendering, all offline (no network, no live LLM CLI).
"""

import json
from pathlib import Path

from agent.dslgen import codeql_source_scan, joern_source_scan
from agent.dslgen.extract import (
    ExtractedDsl,
    _parse_extract_response,
    _parse_subset_response,
    render_harness,
)


def test_scan_finds_documented_and_undocumented_defs(tmp_path):
    src = tmp_path / "joern"
    lang_dir = src / "language" / "src" / "main" / "scala" / "io" / "shiftleft"
    lang_dir.mkdir(parents=True)
    (lang_dir / "Steps.scala").write_text(
        """
package io.shiftleft.language

class NodeSteps {
  /** Filter by exact name. */
  def name(n: String): NodeSteps = this

  def code(re: String): NodeSteps = this

  private def internalHelper(): Unit = ()
}
""",
        encoding="utf-8",
    )
    scanned = joern_source_scan.scan(src)
    names = {e["name"] for e in scanned}
    assert "name" in names and "code" in names
    assert "internalHelper" not in names  # private members excluded

    name_entry = next(e for e in scanned if e["name"] == "name")
    assert "Filter by exact name" in name_entry["doc"]
    assert name_entry["file"].endswith("Steps.scala")


def test_scan_ignores_irrelevant_directories(tmp_path):
    src = tmp_path / "joern"
    other_dir = src / "unrelated_module" / "src"
    other_dir.mkdir(parents=True)
    (other_dir / "Foo.scala").write_text("def bar(): Unit = ()\n", encoding="utf-8")
    assert joern_source_scan.scan(src) == []


def test_scan_missing_dir_raises(tmp_path):
    try:
        joern_source_scan.scan(tmp_path / "does-not-exist")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


def test_parse_extract_response_json_then_grammar_fence():
    raw = """
Here is the catalog:
```json
[{"step": "name", "category": "filter", "signature": ".name(re)", "description": "match name"}]
```
And the grammar:
```text
<query> ::= <traversal>
```
"""
    catalog, grammar = _parse_extract_response(raw)
    assert catalog == [{"step": "name", "category": "filter", "signature": ".name(re)",
                         "description": "match name"}]
    assert grammar == "<query> ::= <traversal>"


def test_parse_extract_response_missing_json_raises():
    try:
        _parse_extract_response("no fences here at all")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_parse_subset_response():
    raw = '```json\n{"keep": ["name", "code"], "justification": "core filters"}\n```'
    keep, justification = _parse_subset_response(raw)
    assert keep == ["name", "code"]
    assert justification == "core filters"


def test_render_harness_groups_by_category_and_reports_reduction():
    extracted = ExtractedDsl(
        full_catalog=[
            {"step": "name", "category": "filter", "signature": ".name(re)", "description": "d1"},
            {"step": "reachableByFlows", "category": "dataflow",
             "signature": ".reachableByFlows(src)", "description": "d2"},
            {"step": "dropped", "category": "other", "signature": ".dropped()", "description": "d3"},
        ],
        subset_catalog=[
            {"step": "name", "category": "filter", "signature": ".name(re)", "description": "d1"},
            {"step": "reachableByFlows", "category": "dataflow",
             "signature": ".reachableByFlows(src)", "description": "d2"},
        ],
        grammar="<query> ::= <traversal>",
        keep_justification="kept the essentials",
    )
    out = render_harness(extracted, title="CPGQL DSL reference (Joern, Scala)",
                         manual_ref="joern_dsl.manual.txt")
    assert "2/3 constructs kept" in out
    assert "#### dataflow" in out and "#### filter" in out
    assert "dropped" not in out
    assert "<query> ::= <traversal>" in out


def test_codeql_scan_finds_class_and_predicate(tmp_path):
    repo = tmp_path / "codeql-repo"
    dataflow_dir = repo / "python" / "ql" / "lib" / "semmle" / "python" / "dataflow" / "new"
    dataflow_dir.mkdir(parents=True)
    (dataflow_dir / "RemoteFlowSources.qll").write_text(
        """
/** A data flow source of remote user input. */
class RemoteFlowSource extends DataFlow::Node {
  RemoteFlowSource() { none() }
}

predicate isSourceLike(DataFlow::Node n) { none() }

private class InternalHelper extends DataFlow::Node { InternalHelper() { none() } }
""",
        encoding="utf-8",
    )
    scanned = codeql_source_scan.scan(repo, "python")
    names = {e["name"] for e in scanned}
    assert "RemoteFlowSource" in names and "isSourceLike" in names
    assert "InternalHelper" not in names  # private excluded

    entry = next(e for e in scanned if e["name"] == "RemoteFlowSource")
    assert "remote user input" in entry["doc"]
    assert entry["file"].endswith("RemoteFlowSources.qll")


def test_codeql_scan_ignores_irrelevant_directories(tmp_path):
    repo = tmp_path / "codeql-repo"
    other_dir = repo / "python" / "ql" / "lib" / "semmle" / "python" / "unrelated"
    other_dir.mkdir(parents=True)
    (other_dir / "Foo.qll").write_text("class Bar extends TUnit { }\n", encoding="utf-8")
    assert codeql_source_scan.scan(repo, "python") == []


def test_codeql_scan_ignores_test_directories(tmp_path):
    # a checkout path containing "test" anywhere must not cause files to be skipped —
    # regression for the same bug once found in joern_source_scan (path checked absolutely
    # instead of relative to the scan root).
    repo = tmp_path / "test_checkout_dir" / "codeql-repo"
    dataflow_dir = repo / "python" / "ql" / "lib" / "semmle" / "python" / "dataflow"
    dataflow_dir.mkdir(parents=True)
    (dataflow_dir / "Foo.qll").write_text("class Bar extends TUnit { }\n", encoding="utf-8")
    scanned = codeql_source_scan.scan(repo, "python")
    assert any(e["name"] == "Bar" for e in scanned)


def test_codeql_scan_unsupported_language_raises(tmp_path):
    try:
        codeql_source_scan.scan(tmp_path, "ruby")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported language")


def test_codeql_scan_finds_value_returning_accessor(tmp_path):
    # QL only requires the `predicate` keyword for boolean-valued members — a value-returning
    # accessor like `getAQueryArgument()` is declared return-type-first, with no
    # `predicate`/`class` keyword at all. This is a regression test mirroring the real
    # DatabaseAccess/getAQueryArgument pair in javascript/ql/lib/semmle/javascript/Concepts.qll,
    # whose absence from the harness meant the agent had no way to know how to pull the query
    # string out of a DatabaseAccess sink.
    repo = tmp_path / "codeql-repo"
    dataflow_dir = repo / "python" / "ql" / "lib" / "semmle" / "python" / "dataflow"
    dataflow_dir.mkdir(parents=True)
    (dataflow_dir / "Concepts.qll").write_text(
        """
/** A data flow node that performs a database access. */
abstract class DatabaseAccess extends DataFlow::Node {
  /** Gets an argument to this database access that is interpreted as a query. */
  abstract DataFlow::Node getAQueryArgument();
}
""",
        encoding="utf-8",
    )
    scanned = codeql_source_scan.scan(repo, "python")
    names = {e["name"]: e for e in scanned}
    assert "DatabaseAccess" in names and "getAQueryArgument" in names
    accessor = names["getAQueryArgument"]
    assert accessor["signature"] == "DataFlow::Node getAQueryArgument()"
    assert accessor["doc"] == "Gets an argument to this database access that is interpreted as a query."


def test_codeql_scan_doc_does_not_bleed_across_unrelated_declarations(tmp_path):
    # regression: the doc-lookup must never span multiple unrelated /** ... */ blocks just
    # because the text between an earlier doc and this declaration doesn't itself match any
    # scanned pattern (a plain field, an unrelated line, etc.).
    repo = tmp_path / "codeql-repo"
    dataflow_dir = repo / "python" / "ql" / "lib" / "semmle" / "python" / "dataflow"
    dataflow_dir.mkdir(parents=True)
    (dataflow_dir / "Foo.qll").write_text(
        """
/** Unrelated leading doc that must not attach to Bar. */
class SomethingElse { }

/** The real doc for Bar. */
class Bar extends TUnit { }
""",
        encoding="utf-8",
    )
    scanned = codeql_source_scan.scan(repo, "python")
    bar = next(e for e in scanned if e["name"] == "Bar")
    assert bar["doc"] == "The real doc for Bar."
    assert "Unrelated" not in bar["doc"]


def test_codeql_scan_accessor_with_no_preceding_doc(tmp_path):
    repo = tmp_path / "codeql-repo"
    dataflow_dir = repo / "python" / "ql" / "lib" / "semmle" / "python" / "dataflow"
    dataflow_dir.mkdir(parents=True)
    (dataflow_dir / "Foo.qll").write_text(
        "abstract class X extends DataFlow::Node {\n"
        "  abstract DataFlow::Node getAResult();\n"
        "}\n",
        encoding="utf-8",
    )
    scanned = codeql_source_scan.scan(repo, "python")
    entry = next(e for e in scanned if e["name"] == "getAResult")
    assert entry["doc"] == ""
