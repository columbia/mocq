"""Unit tests for hover.py — catalog parsing and word-at-position, no live server needed."""

from pathlib import Path

from hover import hover_markdown, load_catalog, word_at

_CATALOG_TEXT = """
### Grammar
```
<query> ::= <root>
```

### Subset catalog

#### dataflow
- `def reachableByFlows[A](sourceTrav: IterableOnce[A]*)(implicit ctx: EngineContext): Traversal[Path]` — Find complete data-flow paths from sink(s) back to given source(s).

#### source
- `cpg.call: Traversal[Call]` — All call sites; typical starting point for sinks/sources by callee name.
- `cpg: Cpg` — Root object referencing the Code Property Graph being queried.
"""


def test_load_catalog_extracts_def_name(tmp_path):
    p = tmp_path / "joern_dsl.txt"
    p.write_text(_CATALOG_TEXT, encoding="utf-8")
    catalog = load_catalog(p)
    assert "reachableByFlows" in catalog
    sig, desc = catalog["reachableByFlows"]
    assert sig.startswith("def reachableByFlows")
    assert "data-flow paths" in desc


def test_load_catalog_extracts_dotted_and_bare_names(tmp_path):
    p = tmp_path / "joern_dsl.txt"
    p.write_text(_CATALOG_TEXT, encoding="utf-8")
    catalog = load_catalog(p)
    assert "call" in catalog and catalog["call"][0] == "cpg.call: Traversal[Call]"
    assert "cpg" in catalog and catalog["cpg"][0] == "cpg: Cpg"


def test_load_catalog_missing_file_returns_empty(tmp_path):
    assert load_catalog(tmp_path / "does-not-exist.txt") == {}


def test_word_at_finds_identifier_under_cursor():
    text = "def mocqSink = cpg.call.reachableByFlows(mocqSource)"
    # position inside "reachableByFlows"
    col = text.index("reachableByFlows") + 3
    assert word_at(text, 0, col) == "reachableByFlows"


def test_word_at_out_of_range_returns_none():
    assert word_at("abc", 5, 0) is None
    assert word_at("abc", 0, 100) is None  # past end of identifiers on the line


def test_hover_markdown_formats_signature_and_description():
    catalog = {"call": ("cpg.call: Traversal[Call]", "All call sites.")}
    md = hover_markdown(catalog, "call")
    assert "```scala" in md and "cpg.call: Traversal[Call]" in md and "All call sites." in md


def test_hover_markdown_none_for_unknown_word():
    assert hover_markdown({}, "nope") is None
    assert hover_markdown({"x": ("x", "y")}, None) is None
