"""
Joern implementation of QueryEngine.

Joern runs as a persistent server (MoCQ's "Query Execution Server"): one CPG is
loaded per language, then every validate / score / flow / probe call is a CPGQL
query against it. A query is a Scala snippet that MUST define top-level
`mocqSource` / `mocqSink` (and optionally `mocqSanitizer`) traversals — the
orchestrator composes evaluation and probe queries from those, exactly like the
CodeQL engine composes over its `mocqSource` / `mocqSink` predicates.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from ._joern_server import JoernServer
from .base import DbResult, FlowPath, FlowStep, ProbeResult, QueryEngine, ValidateResult

_JOERN_LANG = {
    "php": "php",
    "javascript": "jssrc",
    "typescript": "jssrc",
    "python": "pythonsrc",
    "java": "javasrc",
}

_OK = "MOCQ__VALIDATE__OK"
_ERR_MARKERS = ("error found", "-- [E", "not a member of", "not found:", "Compilation Failed")


def _norm(p: str) -> str:
    return Path(str(p)).as_posix().lstrip("./")


def _has_def(text: str, name: str) -> bool:
    return re.search(rf"\bdef\s+{name}\b", text) is not None


class JoernEngine(QueryEngine):
    name = "joern"
    query_extension = ".scala"

    def __init__(self, *, joern_home: str = "", **_):
        self.joern_home = joern_home
        self._bin = str(Path(joern_home) / "joern") if joern_home else "joern"
        self._parse = str(Path(joern_home) / "joern-parse") if joern_home else "joern-parse"
        self._server: Optional[JoernServer] = None

    # ------------------------------------------------------------------ #
    # database lifecycle
    # ------------------------------------------------------------------ #

    def build_database(self, source_root: Path, language: str, db_path: Path) -> DbResult:
        source_root = Path(source_root).resolve()
        db_path = Path(db_path).resolve()
        lang = _JOERN_LANG.get(language)
        if lang is None:
            return DbResult(ok=False, db_path=db_path, error=f"unsupported language {language!r}")

        if db_path.exists():
            shutil.rmtree(db_path, ignore_errors=True)
        db_path.mkdir(parents=True)
        cpg = db_path / "cpg.bin"

        cmd = [self._parse, "--language", lang, "--output", str(cpg), str(source_root)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return DbResult(ok=False, db_path=db_path, error="joern-parse timed out")
        ok = r.returncode == 0 and cpg.is_file()
        return DbResult(
            ok=ok, db_path=db_path, stdout=r.stdout, stderr=r.stderr,
            error="" if ok else (r.stderr or r.stdout or "joern-parse failed"),
        )

    def slice_file(
        self, db_path: Path, rel_path: str, timeout: int = 60, *,
        max_depth: int = 4, max_nodes: int = 2000,
    ) -> Optional[dict]:
        """
        Bounded interprocedural program slice (MoCQ §3.2) of `rel_path`, computed directly against the
        already-open persistent server — no subprocess, no `joern-slice` CLI.

        The stock `joern-slice` path can perform an unbounded interprocedural walk and become
        impractical on real applications. This implementation uses explicit depth and node
        caps, follows incoming REACHING_DEF dependencies across method/file boundaries, and
        returns the same JSON shape consumed by `agent/synth/slicing.py`. Returns ``None`` on
        any failure — callers must fall back to the raw file.
        """
        if self._server is None:
            return None
        if max_depth < 1 or max_nodes < 1:
            raise ValueError("max_depth and max_nodes must be positive")
        esc = rel_path.replace("\\", "\\\\").replace('"', '\\"')
        block = f"""{{
  val __target = "{esc}"
  val __seeds = cpg.file.l.filter(_.name.endsWith(__target)).iterator.ast.isCfgNode.l
  val __data = __seeds.repeat(_.ddgIn)(_.emit.maxDepth({int(max_depth)})).l
  val __nodes = (__seeds ++ __data).dedup.take({int(max_nodes)}).l
  val __idSet = __nodes.map(_.id()).toSet
  val __edgeTypes = Set("REACHING_DEF", "CDG")
  val __edges = __nodes.iterator.inE
    .filter(e => __edgeTypes.contains(e.label) && __idSet.contains(e.src.id()))
    .map(e => (e.src.id(), e.dst.id(), e.label)).l
  def __name(n: CfgNode): String = n match {{ case c: Call => c.name; case _ => "" }}
  val __nodeJson = __nodes.map(n => ujson.Obj(
    "id" -> n.id(), "label" -> n.label, "code" -> n.code.take(300), "name" -> __name(n),
    "lineNumber" -> n.lineNumber.map(_.toInt).getOrElse(-1), "parentMethod" -> n.method.fullName
  )).l
  val __edgeJson = __edges.map(e => ujson.Obj("src" -> e._1, "dst" -> e._2, "label" -> e._3)).l
  ujson.write(ujson.Obj("nodes" -> __nodeJson, "edges" -> __edgeJson))
}}"""
        try:
            out = self._server.execute(block, timeout=timeout)["stdout"]
        except Exception:  # noqa: BLE001 - slicing is best-effort, never fatal
            return None
        if any(m in out for m in _ERR_MARKERS):
            return None
        data = _extract_json(out)
        return data if isinstance(data, dict) else None

    def open_database(self, db_path: Path, language: str) -> None:
        cpg = Path(db_path) / "cpg.bin"
        if not cpg.is_file():
            raise RuntimeError(f"joern CPG not found: {cpg}")
        self.close()
        ws = str(Path(db_path).resolve().parent)  # .dbcache/ — keeps joern's ./workspace out of the repo
        server = JoernServer(self._bin, cwd=ws)
        try:
            server.start()
        except Exception:
            server.stop()
            server = JoernServer(self._bin, cwd=ws)
            server.start()
        res = server.import_cpg(cpg)
        if any(m in res["stdout"] for m in _ERR_MARKERS):
            server.stop()
            raise RuntimeError(f"importCpg failed:\n{res['stdout'][-2000:]}")
        self._server = server

    def close(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None

    # ------------------------------------------------------------------ #
    # query evaluation  (composition strings emit JSON via ujson)
    # ------------------------------------------------------------------ #

    def _eval_json(self, query_text: str, composition: str, timeout: int = 900):
        if self._server is None:
            raise RuntimeError("joern server not started (call open_database first)")
        block = "{\n" + query_text.strip() + "\n\n" + composition.strip() + "\n}"
        out = self._server.execute(block, timeout=timeout)["stdout"]
        if any(m in out for m in _ERR_MARKERS):
            raise RuntimeError(f"joern query error:\n{out[-2500:]}")
        return _extract_json(out)

    def validate_query(self, query_path: Path) -> ValidateResult:
        text = Path(query_path).read_text(encoding="utf-8")
        if self._server is None:
            return ValidateResult(ok=True)  # nothing to validate against yet
        # trailing value expression (not println — the REPL only captures `val resN = ...`)
        block = "{\n" + text.strip() + f'\n"{_OK}"\n}}'
        try:
            out = self._server.execute(block, timeout=180)["stdout"]
        except Exception as exc:
            return ValidateResult(ok=False, errors=str(exc)[:2000], infrastructure_error=True)
        if _OK in out and not any(m in out for m in _ERR_MARKERS):
            return ValidateResult(ok=True)
        return ValidateResult(ok=False, errors=_error_region(out))

    def _flows_expr(self, text: str) -> str:
        """`reachableByFlows`, minus any path that passes through mocqSanitizer."""
        base = "mocqSink.reachableByFlows(mocqSource)"
        if _has_def(text, "mocqSanitizer"):
            return (
                "{ val __san = mocqSanitizer.id.toSet; "
                f"{base}.filterNot(p => p.elements.exists(e => __san.contains(e.id))) }}"
            )
        return base

    def run_query(self, query_path: Path, db_path: Path, source_root: Path) -> Set[str]:
        text = Path(query_path).read_text(encoding="utf-8")
        comp = (
            f"ujson.write({self._flows_expr(text)}"
            ".flatMap(p => p.elements.lastOption).flatMap(_.file.name).dedup.l)"
        )
        data = self._eval_json(text, comp) or []
        root = Path(source_root).resolve()
        return {_relpath(x, root) for x in data if isinstance(x, str)}

    def run_query_paths(
        self, query_path: Path, db_path: Path, source_root: Path
    ) -> Dict[str, List[FlowPath]]:
        text = Path(query_path).read_text(encoding="utf-8")
        comp = (
            f"ujson.write({self._flows_expr(text)}.map(p => "
            "p.elements.map(e => ujson.Obj("
            '"file" -> e.file.name.headOption.getOrElse("?"), '
            '"line" -> e.lineNumber.map(_.toInt).getOrElse(-1), '
            '"code" -> e.code.replace("\\n", " ").take(160)'
            ")).toList).l)"
        )
        data = self._eval_json(text, comp) or []
        root = Path(source_root).resolve()
        by_file: Dict[str, List[FlowPath]] = {}
        for raw_path in data:
            steps: FlowPath = []
            for e in raw_path or []:
                f = _relpath(str(e.get("file", "?")), root)
                try:
                    line = int(e.get("line", -1))
                except (TypeError, ValueError):
                    line = -1
                steps.append(FlowStep(f, line, str(e.get("code", "")).strip()))
            if steps:
                by_file.setdefault(steps[-1].file, []).append(steps)
        return by_file

    def probe_missed(
        self, query_path: Path, db_path: Path, source_root: Path, files: List[str]
    ) -> Dict[str, ProbeResult]:
        text = Path(query_path).read_text(encoding="utf-8")
        results: Dict[str, ProbeResult] = {f: ProbeResult() for f in files}
        if not (_has_def(text, "mocqSource") and _has_def(text, "mocqSink")):
            for f in files:
                results[f] = ProbeResult(
                    unavailable=True,
                    note="define top-level `def mocqSource` and `def mocqSink` traversals "
                         "so misses can be decomposed",
                )
            return results

        root = Path(source_root).resolve()
        src_hits = self._probe_locations(text, "mocqSource", root)
        snk_hits = self._probe_locations(text, "mocqSink", root)
        # engine hook: forked dataflow exposes the stalled partial paths; stock Joern falls
        # back to a line-level reachability frontier.
        partial = self._partial_flows(text, root)
        frontier = {} if partial else self._taint_frontier(text, root)
        trace = self._runtime_trace(text, root)
        for f in files:
            key = _norm(f)
            pf = partial.get(key, [])
            deepest = max(pf, key=len) if pf else []
            results[f] = ProbeResult(
                source_lines=sorted(src_hits.get(key, set())),
                sink_lines=sorted(snk_hits.get(key, set())),
                frontier_lines=(
                    sorted({s.line for p in pf for s in p if s.line > 0})
                    if pf else sorted(frontier.get(key, set()))
                ),
                frontier_path=deepest,
                trace=trace.get(key, []),
            )
        return results

    def _runtime_trace(self, text: str, root: Path) -> Dict[str, List[dict]]:
        """Collect Joern-observable state while evaluating a MoCQ query.

        Joern's public query API does not expose the private interpreter
        handlers used by a particular Joern build.  We therefore instrument
        the adapter at the same semantic boundaries: source, sink, optional
        sanitizer, and each node in the resulting data-flow traversal.  The
        trace records the block, file, line, and node code observed by Joern.
        It is intentionally best-effort so tracing can never make synthesis
        fail.
        """
        out: Dict[str, List[dict]] = {}

        def add(block: str, rows) -> None:
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                rel = _relpath(str(row.get("file", "?")), root)
                try:
                    line = int(row.get("line", -1))
                except (TypeError, ValueError):
                    line = -1
                out.setdefault(rel, []).append({
                    "block": block,
                    "line": line,
                    "code": str(row.get("code", "")).strip(),
                })

        def nodes(traversal: str):
            comp = (
                f"ujson.write({traversal}.flatMap(e => e.file.name.map(n => "
                'ujson.Obj("file" -> n, "line" -> e.lineNumber.map(_.toInt).getOrElse(-1), '
                '"code" -> e.code.replace("\\n", " ").take(200)))).l)'
            )
            try:
                return self._eval_json(text, comp, timeout=600)
            except RuntimeError:
                return []

        add("mocqSource", nodes("mocqSource"))
        add("mocqSink", nodes("mocqSink"))
        if _has_def(text, "mocqSanitizer"):
            add("mocqSanitizer", nodes("mocqSanitizer"))

        # A flow trace is the interpreter-visible result of the composed
        # source-to-sink block.  It also captures cross-file transitions.
        comp = (
            f"ujson.write({self._flows_expr(text)}.flatMap(p => p.elements.map(e => "
            'ujson.Obj("file" -> e.file.name.headOption.getOrElse("?"), '
            '"line" -> e.lineNumber.map(_.toInt).getOrElse(-1), '
            '"code" -> e.code.replace("\\n", " ").take(200)))).l)'
        )
        try:
            add("flow", self._eval_json(text, comp, timeout=600))
        except RuntimeError:
            pass
        return out

    def _partial_flows(self, text: str, root: Path) -> Dict[str, List[FlowPath]]:
        """
        `mocqSink.reachableByPartialFlows(mocqSource)` — the data-flow solver's stalled paths
        that never reached a source. Only present when Joern is built with
        `patches/joern-mocq-partial-flow.patch`; on a stock build the query errors and the
        caller falls back to `_taint_frontier`.
        """
        comp = (
            "ujson.write(mocqSink.reachableByPartialFlows(mocqSource).map(p => "
            "p.elements.map(e => ujson.Obj("
            '"file" -> e.file.name.headOption.getOrElse("?"), '
            '"line" -> e.lineNumber.map(_.toInt).getOrElse(-1), '
            '"code" -> e.code.replace("\\n", " ").take(160)'
            ")).toList).l)"
        )
        try:
            data = self._eval_json(text, comp, timeout=600) or []
        except RuntimeError:
            return {}  # stock build: method absent
        root = Path(root).resolve()
        out: Dict[str, List[FlowPath]] = {}
        for raw in data:
            steps: FlowPath = []
            for e in raw or []:
                try:
                    line = int(e.get("line", -1))
                except (TypeError, ValueError):
                    line = -1
                steps.append(FlowStep(_relpath(str(e.get("file", "?")), root), line,
                                      str(e.get("code", "")).strip()))
            if steps:
                out.setdefault(steps[-1].file, []).append(steps)
        return out

    def _taint_frontier(self, text: str, root: Path) -> Dict[str, Set[int]]:
        """
        Every node the taint from `mocqSource` actually reaches, per file — the
        propagation frontier (Joern's stock `reachableBy` over a broad node set,
        no fork). Lets a miss be reported as "reached line 5, stalled before the
        sink at line 8" rather than just "no flow".
        """
        comp = (
            "ujson.write((cpg.identifier ++ cpg.call.argument ++ cpg.literal)"
            ".reachableBy(mocqSource)"
            ".flatMap(e => e.file.name.map(n => "
            "ujson.Arr(n, e.lineNumber.map(_.toInt).getOrElse(-1)))).dedup.l)"
        )
        try:
            data = self._eval_json(text, comp, timeout=600) or []
        except RuntimeError:
            return {}
        hits: Dict[str, Set[int]] = {}
        for row in data:
            if isinstance(row, list) and len(row) >= 2:
                try:
                    hits.setdefault(_relpath(str(row[0]), root), set()).add(int(row[1]))
                except (TypeError, ValueError):
                    pass
        return hits

    def _probe_locations(self, text: str, trav: str, root: Path) -> Dict[str, Set[int]]:
        comp = (
            f"ujson.write({trav}.flatMap(e => e.file.name.map(n => "
            'ujson.Arr(n, e.lineNumber.map(_.toInt).getOrElse(-1)))).dedup.l)'
        )
        try:
            data = self._eval_json(text, comp) or []
        except RuntimeError:
            return {}
        hits: Dict[str, Set[int]] = {}
        for row in data:
            if not isinstance(row, list) or len(row) < 2:
                continue
            try:
                hits.setdefault(_relpath(str(row[0]), root), set()).add(int(row[1]))
            except (TypeError, ValueError):
                pass
        return hits

    # ------------------------------------------------------------------ #
    # session / prompt
    # ------------------------------------------------------------------ #

    def prepare_workspace(self, work_dir: Path, language: str) -> Optional[str]:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        cfg = self.mcp_server_config()
        if not cfg:
            return None
        mcp_path = work_dir / ".mcp.json"
        mcp_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return str(mcp_path)

    def mcp_server_config(self) -> Optional[dict]:
        server = Path(__file__).resolve().parents[2] / "joern-mcp" / "server.py"
        if not server.exists():
            return None
        env = {}
        if self.joern_home:
            env["JOERN_HOME"] = self.joern_home
        return {
            "mcpServers": {
                "joern": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": [str(server)],
                    "env": env,
                }
            }
        }

    def session_allowed_tools(self) -> list[str]:
        return ["mcp__joern__joern_compile", "mcp__joern__joern_dsl", "Read", "Write"]

    def session_env(self) -> dict:
        return {"JOERN_HOME": self.joern_home} if self.joern_home else {}

    def dialect_hints(self, language: str) -> str:
        pd = Path(__file__).resolve().parents[1] / "prompts"
        parts = []
        dsl = pd / "joern_dsl.txt"
        if dsl.exists():
            parts.append(dsl.read_text(encoding="utf-8").strip())
        lang = pd / f"dialect_joern_{language}.txt"
        if lang.exists():
            parts.append(lang.read_text(encoding="utf-8").strip())
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------------- #

def _relpath(raw: str, root: Path) -> str:
    raw = (raw or "").strip().strip('"')
    try:
        p = Path(raw)
        if p.is_absolute():
            return p.resolve().relative_to(root).as_posix()
    except ValueError:
        pass
    return raw.lstrip("./")


def _extract_json(out: str):
    """
    A `ujson.write(...)` result is echoed by the Joern REPL as either
        val resN: String = "[...]"          (short, with \\" \\n escapes)
        val resN: String = \"\"\"[...]\"\"\"     (long / contains quotes, raw)
    Recover the JSON from the last such `val ...: String = ...` line.
    """
    for m in reversed(list(re.finditer(r"val\s+\w+:\s*String\s*=\s*", out))):
        rhs = out[m.end():].lstrip()
        if rhs.startswith('"""'):
            end = rhs.find('"""', 3)
            body = rhs[3:end] if end != -1 else rhs[3:]
        elif rhs.startswith('"'):
            mm = re.match(r'"((?:[^"\\]|\\.)*)"', rhs, re.DOTALL)
            if not mm:
                continue
            body = (mm.group(1).replace('\\"', '"').replace("\\n", "\n")
                    .replace("\\t", "\t").replace("\\\\", "\\"))
        else:
            continue
        body = body.strip()
        if body[:1] in ("[", "{"):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                continue
    # last resort: a bare JSON array/object anywhere in the output
    mm = re.search(r"(\[.*\]|\{.*\})", out, re.DOTALL)
    if mm:
        try:
            return json.loads(mm.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _error_region(out: str) -> str:
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if any(m in ln for m in _ERR_MARKERS):
            return "\n".join(lines[max(0, i - 2): i + 12])[:2000]
    return out.strip()[-2000:] or "joern query did not confirm ok"
