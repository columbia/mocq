"""
Thin driver for a persistent `joern --server` process (MoCQ's "Query Execution
Server"). Ported from ~/mocq/joern_util.py::Joern with a free-port picker and
process-group teardown.
"""

from __future__ import annotations

import re
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from ._cpgqls import CPGQLSClient

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _clean(s: str) -> str:
    return _ANSI.sub("", s or "").strip()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class JoernServer:
    def __init__(self, joern_bin: str, host: str = "127.0.0.1",
                 port: Optional[int] = None, cwd: Optional[str] = None):
        self.joern_bin = joern_bin
        self.host = host
        self.port = port or _free_port()
        self.cwd = cwd  # joern drops a ./workspace here; keep it out of the repo root
        self.process: Optional[subprocess.Popen] = None
        self.client: Optional[CPGQLSClient] = None

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    def start(self, ready_timeout: float = 90.0) -> None:
        if self.cwd:
            Path(self.cwd).mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [self.joern_bin, "--server", "--server-host", self.host,
             "--server-port", str(self.port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, cwd=self.cwd,
        )
        endpoint = f"{self.host}:{self.port}"
        deadline = time.time() + ready_timeout
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            if self.process.poll() is not None:
                out = self.process.stdout.read().decode("utf-8", "replace") if self.process.stdout else ""
                raise RuntimeError(f"joern server exited early (rc={self.process.returncode}):\n{out[-2000:]}")
            try:
                client = CPGQLSClient(endpoint)
                client.execute('"probe"')
                self.client = client
                return
            except Exception as exc:  # not up yet
                last_err = exc
                time.sleep(1.5)
        self.stop()
        raise RuntimeError(f"joern server did not become ready in {ready_timeout}s ({last_err})")

    def stop(self) -> None:
        self.client = None
        proc = self.process
        self.process = None
        if not proc or proc.poll() is not None:
            return
        import os
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                return
            try:
                proc.wait(timeout=8)
                return
            except subprocess.TimeoutExpired:
                continue

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # queries
    # ------------------------------------------------------------------ #

    def execute(self, query: str, timeout: int = 900) -> dict:
        """Return {'stdout': str, 'stderr': str, 'success': bool} with ANSI stripped."""
        if self.client is None:
            raise RuntimeError("joern server not started")
        res = self.client.execute(query, timeout=timeout)
        return {
            "stdout": _clean(res.get("stdout", "")),
            "stderr": _clean(res.get("stderr", "")),
            "success": bool(res.get("success", True)),
        }

    def import_cpg(self, cpg_path: Path) -> dict:
        return self.execute(f'importCpg("{Path(cpg_path).resolve().as_posix()}")')
