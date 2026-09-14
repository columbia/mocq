"""
CodeQL implementation of QueryEngine.

Wraps three CLI operations:
  database create   build-free extraction for python / javascript
  query compile     --check-only syntax/type validation
  database analyze  --format=csv, mapped down to the set of flagged files

The CodeQL LSP MCP server (``codeql-lsp-mcp``) is exposed to the agent session
for on-demand ``codeql_compile`` / ``codeql_hover`` / ``codeql_diagnostics``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urlparse
from pathlib import Path
from typing import Dict, List, Optional, Set

from .base import DbResult, FlowPath, FlowStep, ProbeResult, QueryEngine, ValidateResult

# all three run with --build-mode none; typescript is handled by the js extractor
_CODEQL_LANG = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "javascript",
    "java": "java",
}
_QLPACK_DEP = {
    "python": "codeql/python-all",
    "javascript": "codeql/javascript-all",
    "java": "codeql/java-all",
}
_IMPORT_LINE = {"python": "import python", "javascript": "import javascript", "java": "import java"}

_INFRA_MARKERS = (
    "Could not list directory /proc/",
    "java.nio.file.FileSystemException: /proc/",
    "bwrap: No permissions to create a new namespace",
)


class CodeQLEngine(QueryEngine):
    name = "codeql"
    query_extension = ".ql"

    def __init__(self, *, tool_command: str = "codeql", search_path: str = "", lsp_mcp_path: str = ""):
        self.tool_command = tool_command
        self.search_path = search_path
        self.lsp_mcp_path = lsp_mcp_path

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _env(self) -> dict:
        env = os.environ.copy()
        env.setdefault("CODEQL_ALLOW_INSTALLATION_ANYWHERE", "true")
        env["CODEQL_PATH"] = self.tool_command
        if self.search_path:
            env["CODEQL_SEARCH_PATH"] = self.search_path
        return env

    def _search_path_args(self) -> list:
        return ["--search-path", self.search_path] if self.search_path else []

    @staticmethod
    def _is_infra_failure(output: str) -> bool:
        return any(marker in output for marker in _INFRA_MARKERS)

    # ------------------------------------------------------------------ #
    # QueryEngine interface
    # ------------------------------------------------------------------ #

    def build_database(self, source_root: Path, language: str, db_path: Path) -> DbResult:
        source_root = Path(source_root).resolve()
        db_path = Path(db_path).resolve()
        lang = _CODEQL_LANG.get(language)
        if lang is None:
            return DbResult(ok=False, db_path=db_path, error=f"unsupported language {language!r}")

        if db_path.exists():
            shutil.rmtree(db_path, ignore_errors=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.tool_command, "database", "create", str(db_path),
            "--language", lang,
            "--build-mode", "none",
            "--source-root", str(source_root),
            "--overwrite",
            *self._search_path_args(),
        ]
        try:
            r = subprocess.run(
                cmd, cwd=str(source_root), capture_output=True, text=True,
                timeout=900, env=self._env(),
            )
        except subprocess.TimeoutExpired:
            return DbResult(ok=False, db_path=db_path, error="database create timed out")

        return DbResult(
            ok=r.returncode == 0,
            db_path=db_path,
            stdout=r.stdout,
            stderr=r.stderr,
            error="" if r.returncode == 0 else (r.stderr or r.stdout or "database create failed"),
        )

    def validate_query(self, query_path: Path) -> ValidateResult:
        query_path = Path(query_path).resolve()
        cmd = [
            self.tool_command, "query", "compile", "--check-only", str(query_path),
            *self._search_path_args(),
        ]
        try:
            r = subprocess.run(
                cmd, cwd=str(query_path.parent), capture_output=True, text=True,
                timeout=120, env=self._env(),
            )
        except subprocess.TimeoutExpired:
            return ValidateResult(ok=False, errors="query compile timed out")

        combined = "\n".join(p for p in (r.stdout, r.stderr) if p)
        if r.returncode == 0:
            return ValidateResult(ok=True)
        return ValidateResult(
            ok=False,
            errors=combined.strip() or "query compile failed",
            infrastructure_error=self._is_infra_failure(combined),
        )

    def _analyze(self, query_path: Path, db_path: Path, fmt: str, out_name: str) -> str:
        """Run `database analyze` and return the output file's text ("" if empty)."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / out_name
            cmd = [
                self.tool_command, "database", "analyze",
                str(Path(db_path).resolve()), str(Path(query_path).resolve()),
                f"--format={fmt}", f"--output={out}",
                "--threads=0", "--rerun",
                *self._search_path_args(),
            ]
            r = subprocess.run(
                cmd, cwd=str(Path(query_path).parent), capture_output=True, text=True,
                timeout=900, env=self._env(),
            )
            if r.returncode != 0:
                raise RuntimeError(
                    f"codeql database analyze failed (rc={r.returncode}):\n{r.stderr or r.stdout}"
                )
            return out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""

    def run_query(self, query_path: Path, db_path: Path, source_root: Path) -> Set[str]:
        text = self._analyze(Path(query_path), Path(db_path), "csv", "results.csv")
        return self._flagged_files_from_csv(text, Path(source_root).resolve())

    def run_query_paths(
        self, query_path: Path, db_path: Path, source_root: Path
    ) -> Dict[str, List[FlowPath]]:
        text = self._analyze(Path(query_path), Path(db_path), "sarif-latest", "results.sarif")
        return self._paths_from_sarif(text, Path(source_root).resolve())

    def probe_missed(
        self, query_path: Path, db_path: Path, source_root: Path, files: List[str]
    ) -> Dict[str, ProbeResult]:
        query_path = Path(query_path).resolve()
        source_root = Path(source_root).resolve()
        results: Dict[str, ProbeResult] = {f: ProbeResult() for f in files}

        query_text = query_path.read_text(encoding="utf-8")
        source_hits = self._run_probe_query(
            _rewrite_as_probe(query_text, "mocqSource"), "src", query_path, db_path, source_root)
        sink_hits = self._run_probe_query(
            _rewrite_as_probe(query_text, "mocqSink"), "snk", query_path, db_path, source_root)

        if source_hits is None or sink_hits is None:
            for f in files:
                results[f] = ProbeResult(
                    unavailable=True,
                    note="define top-level `predicate mocqSource(DataFlow::Node)` and "
                         "`predicate mocqSink(DataFlow::Node)` so misses can be decomposed",
                )
            return results

        # taint frontier (best-effort — needs a mocqSource predicate; None just means no
        # frontier data, not a hard failure like the two probes above)
        frontier_probe = _rewrite_as_frontier_probe(query_text)
        frontier_hits = self._run_probe_query(
            frontier_probe, "frontier", query_path, db_path, source_root) if frontier_probe else None

        for f in files:
            key = _norm(f)
            results[f] = ProbeResult(
                source_lines=sorted(source_hits.get(key, set())),
                sink_lines=sorted(sink_hits.get(key, set())),
                frontier_lines=sorted((frontier_hits or {}).get(key, set())),
            )
        return results

    def _run_probe_query(
        self, probe_text: Optional[str], tag: str, query_path: Path, db_path: Path, source_root: Path
    ) -> Optional[Dict[str, Set[int]]]:
        """Write+validate+analyze a rewritten probe query; return per-file line sets, or None."""
        if probe_text is None:
            return None
        probe_path = query_path.with_name(f".probe_{tag}{self.query_extension}")
        probe_path.write_text(probe_text, encoding="utf-8")
        try:
            if not self.validate_query(probe_path).ok:
                return None
            text = self._analyze(probe_path, db_path, "csv", "probe.csv")
        except RuntimeError:
            return None
        finally:
            probe_path.unlink(missing_ok=True)
        hits: Dict[str, Set[int]] = {}
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 6:
                continue
            rel = _relpath(row[4], source_root)
            try:
                line = int(row[5])
            except ValueError:
                continue
            if line <= 0:
                continue  # synthetic/location-less node (e.g. a broad frontier-probe sink match)
            hits.setdefault(rel, set()).add(line)
        return hits

    def prepare_workspace(self, work_dir: Path, language: str) -> Optional[str]:
        work_dir = Path(work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)

        qlpack = work_dir / "qlpack.yml"
        dep = _QLPACK_DEP.get(language, "codeql/python-all")
        qlpack.write_text(
            "name: mocq-synth-queries\n"
            "version: 0.1.0\n"
            "dependencies:\n"
            f'  {dep}: "*"\n',
            encoding="utf-8",
        )

        mcp_path = work_dir / ".mcp.json"
        mcp_path.write_text(json.dumps(self.mcp_server_config(), indent=2), encoding="utf-8")
        return str(mcp_path)

    def session_allowed_tools(self) -> list[str]:
        return [
            "mcp__codeql__codeql_compile",
            "mcp__codeql__codeql_diagnostics",
            "mcp__codeql__codeql_hover",
            "mcp__codeql__codeql_definition",
            "Read",
            "Write",
        ]

    def session_env(self) -> dict:
        env = {
            "CODEQL_PATH": self.tool_command,
            "CODEQL_ALLOW_INSTALLATION_ANYWHERE": "true",
        }
        if self.search_path:
            env["CODEQL_SEARCH_PATH"] = self.search_path
        return env

    def mcp_server_config(self) -> dict:
        env = {"CODEQL_PATH": self.tool_command}
        if self.search_path:
            env["CODEQL_SEARCH_PATH"] = self.search_path
        return {
            "mcpServers": {
                "codeql": {
                    "type": "stdio",
                    "command": "node",
                    "args": [self.lsp_mcp_path],
                    "env": env,
                }
            }
        }

    def dialect_hints(self, language: str) -> str:
        hint_file = Path(__file__).resolve().parents[1] / "prompts" / f"dialect_codeql_{language}.txt"
        if hint_file.exists():
            return hint_file.read_text(encoding="utf-8").strip()
        return ""

    # ------------------------------------------------------------------ #
    # result parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _flagged_files_from_csv(text: str, source_root: Path) -> Set[str]:
        """
        CodeQL analyze CSV columns:
        name, description, severity, message, path, start_line, start_col, end_line, end_col
        ``path`` is source-root-relative with a leading '/'.
        """
        flagged: Set[str] = set()
        for row in csv.reader(io.StringIO(text or "")):
            if len(row) < 5 or not row[4].strip():
                continue
            flagged.add(_relpath(row[4], source_root))
        return flagged

    @staticmethod
    def _paths_from_sarif(text: str, source_root: Path) -> Dict[str, List[FlowPath]]:
        """Walk SARIF codeFlows -> {flagged file: [ [FlowStep, ...], ... ]}."""
        out: Dict[str, List[FlowPath]] = {}
        if not text.strip():
            return out
        sarif = json.loads(text)
        for run in sarif.get("runs", []):
            for res in run.get("results", []):
                locs = res.get("locations", [])
                if not locs:
                    continue
                primary = _sarif_loc(locs[0], source_root)
                if primary is None:
                    continue
                bucket = out.setdefault(primary[0], [])
                flows = res.get("codeFlows") or []
                if not flows:
                    bucket.append([FlowStep(primary[0], primary[1], "finding")])
                    continue
                for cf in flows:
                    for tf in cf.get("threadFlows", []):
                        path: FlowPath = []
                        for node in tf.get("locations", []):
                            loc = _sarif_loc(node.get("location", {}), source_root)
                            if loc is None:
                                continue
                            note = (node.get("location", {}).get("message", {}) or {}).get("text", "")
                            path.append(FlowStep(loc[0], loc[1], note.strip()))
                        if path:
                            bucket.append(path)
        return out


# ---------------------------------------------------------------------------- #
# module helpers
# ---------------------------------------------------------------------------- #

def _norm(p: str) -> str:
    return Path(str(p)).as_posix().lstrip("./")


def _relpath(raw: str, source_root: Path) -> str:
    raw = unquote((raw or "").strip())
    # SARIF commonly uses file:// URIs, while CSV output usually uses plain
    # paths.  Normalize both to the same source-root-relative representation.
    parsed = urlparse(raw)
    if parsed.scheme == "file":
        # UNC-style file URIs retain a hostname; local SARIF URIs have an empty
        # netloc.  For the latter, the URI path is the filesystem path.
        raw = f"//{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
    try:
        if Path(raw).is_absolute():
            return Path(raw).resolve().relative_to(Path(source_root).resolve()).as_posix()
    except ValueError:
        pass
    return raw.lstrip("/")


def _sarif_loc(loc: dict, source_root: Path):
    phys = (loc or {}).get("physicalLocation") or {}
    uri = (phys.get("artifactLocation") or {}).get("uri")
    line = (phys.get("region") or {}).get("startLine")
    if not uri or line is None:
        return None
    return _relpath(uri, source_root), int(line)


_PROBE_META = {
    "mocqSource": ("-probe-src", '"source"'),
    "mocqSink": ("-probe-snk", '"sink"'),
}

def _clean_probe_head(query_text: str, id_suffix: str) -> Optional[str]:
    """
    Shared prep for both probe kinds: convert `@kind path-problem` -> `@kind problem`, tag the
    `@id`, and drop PathGraph imports / `query predicate` blocks (a `problem` query must have
    exactly one query clause). Returns None when the query has no trailing `from ...` to replace.
    """
    m = re.search(r"(?ms)^from\s.*\Z", query_text)
    if not m:
        return None
    head = query_text[: m.start()]
    head = re.sub(r"@kind\s+path-problem", "@kind problem", head)
    head = re.sub(r"(@id\s+[^\s*]+)", lambda mm: mm.group(1) + id_suffix, head, count=1)
    kept = []
    skip_block = False
    for line in head.splitlines():
        s = line.strip()
        if re.match(r"import\s+\S*PathGraph\b", s) or re.match(r"import\s+DataFlow::\w*PathGraph", s):
            continue
        if s.startswith("query "):
            skip_block = True
        if skip_block:
            if s.endswith("}") or (s.endswith(";") and "{" not in s) or s == "":
                skip_block = False
            continue
        kept.append(line)
    return "\n".join(kept).rstrip()


def _rewrite_as_probe(query_text: str, predicate: str) -> Optional[str]:
    """
    Turn a taint query into a plain `@kind problem` query that selects just the
    nodes matched by ``predicate`` (mocqSource / mocqSink). Returns None when the
    query has no trailing `from ... select ...` to replace or no such predicate.
    """
    if not re.search(rf"\bpredicate\s+{predicate}\s*\(", query_text):
        return None
    id_suffix, label = _PROBE_META[predicate]
    head = _clean_probe_head(query_text, id_suffix)
    if head is None:
        return None
    return head + "\n\nfrom DataFlow::Node n\nwhere " + predicate + "(n)\nselect n, " + label + "\n"


def _rewrite_as_frontier_probe(query_text: str) -> Optional[str]:
    """
    Turn a taint query into a plain `@kind problem` query that selects every node reachable
    from `mocqSource` via a **universal-sink** instantiation of the engine's own
    `TaintTracking::Global<Config>` — analogous to Joern's stock `_taint_frontier` fallback
    ("how far did the taint spread"), independent of whatever specific sink the query itself
    uses. Returns None when the query has no `mocqSource` predicate or no trailing `from ...`.

    Deliberately reuses the real, complete taint-tracking engine (field-sensitive,
    interprocedural — the same one that powers the query's own real evaluation) rather than a
    hand-rolled single-step relation: CodeQL's public API has no flat "one step of taint
    propagation, including field/attribute reads" predicate outside a `Global<Config>` — the
    field-read/store-read step relations that make e.g. `x.attr` propagate are internal-only.

    `isSink` is deliberately NOT a true universal `any()` — empirically, a truly-universal sink
    makes the engine's field-flow relevance pruning degenerate (it stops after one hop, missing
    exactly the field/attribute-read propagation this is meant to capture; confirmed against a
    real Flask `request.form` source, which stalled at the import line with `any()` but traced
    correctly all the way to the real sink with a real one). Bounding `isSink` to "any node in a
    file that has a source" keeps the engine's pruning meaningful while still being source-
    location-independent (it doesn't need to know the query's real sink).
    """
    if not re.search(r"\bpredicate\s+mocqSource\s*\(", query_text):
        return None
    head = _clean_probe_head(query_text, "-probe-frontier")
    if head is None:
        return None
    return (
        head
        + "\n\nmodule MocqFrontierCfg implements DataFlow::ConfigSig {\n"
        "  predicate isSource(DataFlow::Node n) { mocqSource(n) }\n"
        "  predicate isSink(DataFlow::Node n) {\n"
        "    exists(DataFlow::Node src | mocqSource(src) |\n"
        "      n.getLocation().getFile() = src.getLocation().getFile()\n"
        "    )\n"
        "  }\n"
        "}\n\n"
        "module MocqFrontierFlow = TaintTracking::Global<MocqFrontierCfg>;\n\n"
        "from DataFlow::Node n\n"
        'where MocqFrontierFlow::flow(_, n)\nselect n, "frontier"\n'
    )
