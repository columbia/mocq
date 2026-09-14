"""
QueryEngine — the seam between the engine-neutral synthesis loop and a
concrete static-analysis engine (CodeQL now, Joern later).

The loop only ever calls these methods. Anything engine-specific (query
dialect, database format, tool server) lives in an implementation module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class DbResult:
    ok: bool
    db_path: Path
    stdout: str = ""
    stderr: str = ""
    error: str = ""


@dataclass
class ValidateResult:
    ok: bool
    errors: str = ""
    infrastructure_error: bool = False


@dataclass
class FlowStep:
    file: str          # source-root-relative
    line: int
    note: str = ""     # engine's label for the node (e.g. "ControlFlowNode for request")


#: an ordered source -> ... -> sink path
FlowPath = List[FlowStep]


@dataclass
class ProbeResult:
    """Decomposition of a query for one file: where its source/sink predicates matched."""
    source_lines: List[int] = field(default_factory=list)
    sink_lines: List[int] = field(default_factory=list)
    #: lines the taint from mocqSource actually reached in this file (the "frontier" — how far
    #: propagation got before it stalled). Populated by engines that can trace partial flow.
    frontier_lines: List[int] = field(default_factory=list)
    #: the deepest partial (stalled) source->...->? path in this file, when the engine can produce
    #: one (Joern's forked dataflow hook). Empty otherwise.
    frontier_path: "FlowPath" = field(default_factory=list)
    #: Engine-observable execution trace for the query's MoCQ blocks.  Entries are
    #: deliberately JSON-shaped so they can be shown to the synthesis agent and
    #: persisted in run artifacts (Joern populates this today).
    trace: List[dict] = field(default_factory=list)
    #: True when the query could not be decomposed (no mocqSource/mocqSink predicates, probe
    #: failed to compile, etc.) — the loop then just asks the agent to expose those predicates.
    unavailable: bool = False
    note: str = ""

    def classify(self) -> str:
        if self.unavailable:
            return "no_decomposition"
        if not self.source_lines and not self.sink_lines:
            return "no_source_no_sink"
        if not self.source_lines:
            return "no_source"
        if not self.sink_lines:
            return "no_sink"
        return "source_and_sink_but_no_flow"


class QueryEngine(ABC):
    #: short identifier, matches the config ``engine`` field
    name: str = "base"
    #: file extension for a query in this engine's language
    query_extension: str = ".ql"

    @abstractmethod
    def build_database(self, source_root: Path, language: str, db_path: Path) -> DbResult:
        """Build/refresh an analysis database over every file under ``source_root``."""

    @abstractmethod
    def validate_query(self, query_path: Path) -> ValidateResult:
        """Compile/parse-check a query without running it."""

    @abstractmethod
    def run_query(self, query_path: Path, db_path: Path, source_root: Path) -> Set[str]:
        """
        Run ``query_path`` against ``db_path`` and return the set of
        ``source_root``-relative file paths that receive at least one finding.
        Per-file granularity only — the cheap call used for scoring.
        """

    def run_query_paths(
        self, query_path: Path, db_path: Path, source_root: Path
    ) -> Dict[str, List[FlowPath]]:
        """
        Like ``run_query`` but also return, per flagged file, the concrete
        source -> sink path(s) the engine found. Used only on feedback rounds.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement run_query_paths")

    def probe_missed(
        self, query_path: Path, db_path: Path, source_root: Path, files: List[str]
    ) -> Dict[str, ProbeResult]:
        """
        For files the query did NOT flag, decompose the query and report where its
        source / sink predicates matched, so the agent can tell a missing-source
        problem from a broken-flow problem.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement probe_missed")

    def open_database(self, db_path: Path, language: str) -> None:
        """
        Optional per-language setup before the phases run (e.g. start a query
        server and load the database). No-op for stateless engines.
        """

    def close(self) -> None:
        """Tear down whatever ``open_database`` started. No-op for stateless engines."""

    @abstractmethod
    def prepare_workspace(self, work_dir: Path, language: str) -> Optional[str]:
        """
        Lay out whatever the agent session needs next to ``work_dir`` (e.g. a
        query pack manifest, an MCP config). Returns the path to an MCP config
        file for the CLI session, or ``None`` if the engine has no MCP server.
        """

    def dialect_hints(self, language: str) -> str:
        """Short prompt snippet with engine+language query idioms."""
        return ""

    def mcp_server_config(self) -> Optional[dict]:
        """MCP server block for the agent session, or None if the engine has no MCP server."""
        return None

    def session_allowed_tools(self) -> list[str]:
        """
        Tool allow-list for the agent session. Compile-level only — evaluation is
        the orchestrator's job, so a query-run tool must never appear here.
        """
        return ["Read", "Write"]

    def session_env(self) -> dict:
        """Extra environment variables the agent session's subprocess needs."""
        return {}
