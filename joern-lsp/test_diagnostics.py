"""
Unit tests for diagnostics.parse_diagnostics — fixtures are verbatim captures from a live
Joern REPL (see the module docstring in diagnostics.py for the raw shape). No live server
needed to run these.
"""

from diagnostics import parse_diagnostics, SEVERITY_ERROR, SEVERITY_WARNING

_NOT_FOUND = (
    '-- [E008] Not Found Error: -----------------------------------------------------\n'
    '2 |def mocqSource = cpg.call.nonExistentStepXYZ\n'
    '  |                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
    '  |value nonExistentStepXYZ is not a member of Iterator[io.shiftleft.codepropertygraph.'
    'generated.nodes.Call]\n'
    '1 error found'
)

_SYNTAX = (
    '-- [E018] Syntax Error: --------------------------------------------------------\n'
    '2 |def mocqSource = cpg.call(\n'
    '  |                          ^\n'
    '  |                          expression expected but def found\n'
    '  |-----------------------------------------------------------------------------\n'
    '  | Explanation (enabled by `-explain`)\n'
    '  |- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -\n'
    '  | An expression cannot start with def.\n'
    '   -----------------------------------------------------------------------------'
)

_WARNING = (
    '1 warning found\n'
    'def mocqSource: Iterator[io.shiftleft.codepropertygraph.generated.nodes.Call]\n'
    '-- Deprecation Warning: --------------------------------------------------------\n'
    '3 |def mocqSink = 5 + "x"\n'
    '  |               ^^^\n'
    '  |method + in class Int is deprecated since 2.13.0: Adding a number and a String '
    'is deprecated. Use the string interpolation `s"$num$str"`\n'
    'def mocqSink: String\n'
    'val res1: String = "OK"'
)

_TWO_ERRORS = (
    '-- [E008] Not Found Error: -----------------------------------------------------\n'
    '2 |def mocqSource = cpg.call.nonExistentStepXYZ\n'
    '  |                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
    '  |value nonExistentStepXYZ is not a member of Iterator[Call]\n'
    '-- [E008] Not Found Error: -----------------------------------------------------\n'
    '3 |def mocqSink = cpg.callXYZAlsoBad.argument\n'
    '  |               ^^^^^^^^^^^^^^^^^^\n'
    '  |value callXYZAlsoBad is not a member of Cpg\n'
    '2 errors found'
)

_CLEAN = (
    'def mocqSource: Iterator[io.shiftleft.codepropertygraph.generated.nodes.Call]\n'
    'def mocqSink:\n'
    '  Iterator[io.shiftleft.codepropertygraph.generated.nodes.Expression]\n'
    'val res2: String = "OK"'
)


def test_not_found_error():
    diags = parse_diagnostics(_NOT_FOUND)
    assert len(diags) == 1
    d = diags[0]
    assert d["severity"] == SEVERITY_ERROR
    # wrapped line 2 -> doc line 0 (wrap_prefix_lines=1 default: "{\n" is wrapped line 1)
    assert d["range"]["start"]["line"] == 0
    assert d["range"]["start"]["character"] == 17
    # dotty underlines the whole selector expression "cpg.call.nonExistentStepXYZ" (27 chars),
    # not just the unresolved member name
    assert d["range"]["end"]["character"] == 17 + len("cpg.call.nonExistentStepXYZ")
    assert "not a member of Iterator" in d["message"]


def test_syntax_error_stops_before_explanation():
    diags = parse_diagnostics(_SYNTAX)
    assert len(diags) == 1
    d = diags[0]
    assert d["severity"] == SEVERITY_ERROR
    assert d["message"] == "expression expected but def found"
    assert "Explanation" not in d["message"]


def test_warning_severity_and_line_shift():
    diags = parse_diagnostics(_WARNING)
    assert len(diags) == 1
    d = diags[0]
    assert d["severity"] == SEVERITY_WARNING
    # wrapped line 3 -> doc line 1
    assert d["range"]["start"]["line"] == 1
    assert "deprecated" in d["message"]


def test_two_errors_in_one_pass():
    diags = parse_diagnostics(_TWO_ERRORS)
    assert len(diags) == 2
    assert diags[0]["range"]["start"]["line"] == 0
    assert diags[1]["range"]["start"]["line"] == 1
    assert "nonExistentStepXYZ" in diags[0]["message"]
    assert "callXYZAlsoBad" in diags[1]["message"]


def test_clean_output_has_no_diagnostics():
    assert parse_diagnostics(_CLEAN) == []


def test_wrap_prefix_lines_parameter():
    # if the wrapper prepends 2 lines instead of 1, wrapped-line-2 now maps to doc line -1 —
    # negative doc lines are dropped (they'd point into our own wrapper, not client text)
    assert parse_diagnostics(_NOT_FOUND, wrap_prefix_lines=2) == []
