"""Unit tests for joern-slice JSON -> pseudocode reconstruction (agent/synth/slicing.py)."""

from agent.synth.slicing import reconstruct


def test_empty_slice_returns_empty_string():
    assert reconstruct({"nodes": [], "edges": []}) == ""
    assert reconstruct({}) == ""


def test_reconstructs_simple_assignment_and_call():
    # a minimal captured-shape fixture: `sink(x)` where `x` is reached-by-def from `tainted()`.
    nodes = [
        {"id": 1, "label": "METHOD", "code": "handle", "name": "handle",
         "parentMethod": "login.php:handle", "lineNumber": 1},
        {"id": 2, "label": "METHOD_PARAMETER_IN", "code": "$req",
         "parentMethod": "login.php:handle", "lineNumber": 1},
        {"id": 3, "label": "CALL", "code": "$x = tainted($req)", "name": "<operator>.assignment",
         "parentMethod": "login.php:handle", "lineNumber": 2},
        {"id": 4, "label": "IDENTIFIER", "code": "$x",
         "parentMethod": "login.php:handle", "lineNumber": 3},
        {"id": 5, "label": "CALL", "code": "sink($x)", "name": "sink",
         "parentMethod": "login.php:handle", "lineNumber": 3},
        {"id": 6, "label": "RETURN", "code": "return sink($x)",
         "parentMethod": "login.php:handle", "lineNumber": 4},
    ]
    edges = [
        {"src": 3, "dst": 4, "label": "REACHING_DEF"},
    ]
    out = reconstruct({"nodes": nodes, "edges": edges})
    assert "function handle($req) {" in out
    assert "tainted($req)" in out
    assert "sink($x);" in out


def test_skips_placeholder_only_methods():
    nodes = [
        {"id": 1, "label": "METHOD", "code": "empty", "parentMethod": "a.php:empty", "lineNumber": 1},
        {"id": 2, "label": "METHOD_PARAMETER_IN", "code": "$p", "parentMethod": "a.php:empty", "lineNumber": 1},
    ]
    out = reconstruct({"nodes": nodes, "edges": []})
    assert out == ""


def test_global_nodes_included_without_function_wrapper():
    nodes = [
        {"id": 1, "label": "CALL", "code": "sink($GLOBALS['x'])", "name": "sink",
         "parentMethod": "<global>", "lineNumber": 1},
    ]
    out = reconstruct({"nodes": nodes, "edges": []})
    assert "sink($GLOBALS['x']);" in out
    assert "function" not in out


def test_prefixed_dangerous_call_keeps_its_method_from_being_filtered():
    # Regression: the "interesting method" filter only keeps methods whose own code matches
    # _SECURITY_RE when *something* in the file matched it elsewhere (here, $_POST in the
    # global block) — a real dangerous call like `mysqli_query(...)` must still count as a
    # match even though "query" is a suffix of a longer identifier, not a bare word. Before the
    # fix, `check_login` (containing the actual SQL concatenation + mysqli_query call) was
    # silently dropped from the reconstruction because \bquery\( doesn't match "mysqli_query(".
    nodes = [
        {"id": 1, "label": "METHOD", "code": "function check_login($db, $username)",
         "parentMethod": "check_login", "lineNumber": 2},
        {"id": 2, "label": "METHOD_PARAMETER_IN", "code": "$db",
         "parentMethod": "check_login", "lineNumber": 2},
        {"id": 3, "label": "METHOD_PARAMETER_IN", "code": "$username",
         "parentMethod": "check_login", "lineNumber": 2},
        {"id": 4, "label": "RETURN", "code": "return mysqli_query($db,$q)",
         "parentMethod": "check_login", "lineNumber": 4},
        {"id": 5, "label": "CALL", "code": "check_login($db,$_POST[\"username\"])",
         "name": "check_login", "parentMethod": "a.php:<global>", "lineNumber": 7},
    ]
    out = reconstruct({"nodes": nodes, "edges": []})
    assert "function check_login($db, $username) {" in out
    assert "return mysqli_query($db,$q);" in out
