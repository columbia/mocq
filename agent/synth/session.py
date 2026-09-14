"""
Drive one headless CLI agent session (claude / agy / gemini) with MCP tools.

Lifted from the old ``refinement/cli_backend.py`` streaming logic and reduced to
a single reusable ``run_agent_session`` call. The orchestrator owns query
evaluation, so the session is only ever granted compile-level MCP tools.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# fallback tool surface if the engine does not specify one (engine.session_allowed_tools())
DEFAULT_ALLOWED_TOOLS = "Read,Write"

_CMD_NAME = {"claude_cli": "claude", "claude": "claude", "agy_cli": "agy", "gemini_cli": "gemini"}


@dataclass
class SessionResult:
    returncode: int
    timed_out: bool
    hit_max_turns: bool
    elapsed_seconds: float
    stdout: str
    log_path: str
    stderr_path: str


def resolve_executable(provider: str) -> tuple[str, str]:
    cmd_name = _CMD_NAME.get(provider)
    if not cmd_name:
        raise ValueError(f"unsupported model_provider {provider!r} (use agy_cli / claude_cli / gemini_cli)")
    exe = shutil.which(cmd_name)
    if not exe and cmd_name == "agy":
        local = os.path.expanduser("~/.local/bin/agy")
        if os.path.exists(local):
            exe = local
    if not exe:
        raise RuntimeError(f"{cmd_name} CLI not found on PATH")
    return exe, cmd_name


def register_mcp_servers(provider: str, executable: str, mcp_server_config: Optional[dict]) -> None:
    """
    Make the engine's MCP server(s) visible to the agent CLI.

    claude / gemini read a config file (``--mcp-config`` / ``.gemini/settings.json``);
    agy keeps its own registry, so we ``agy mcp add`` each server (idempotent).
    """
    if provider != "agy_cli" or not mcp_server_config:
        return
    for name, spec in (mcp_server_config.get("mcpServers") or {}).items():
        command = spec.get("command")
        if not command:
            continue
        args = list(spec.get("args") or [])
        flags: List[str] = []
        for key, value in (spec.get("env") or {}).items():
            flags += ["-e", f"{key}={value}"]
        cmd = [executable, "mcp", "add", *flags, name, command, *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        status = "ok" if result.returncode == 0 else f"rc={result.returncode}"
        print(f"   agy mcp add {name}: {status} {result.stderr.strip()[:160]}".rstrip())


def build_cli_command(
    *,
    provider: str,
    executable: str,
    prompt: str,
    mcp_config_path: Optional[str],
    allowed_tools: str,
    max_turns: int,
    timeout: int,
    model: Optional[str],
    env: Dict[str, str],
) -> tuple[List[str], str]:
    """Return ``(argv, stdin_text)``. stdin_text is "" when the prompt goes in argv."""
    if provider in ("claude_cli", "claude"):
        cmd = [
            executable, "--print", "--output-format", "json", "--verbose",
            "--allowedTools", allowed_tools, "--max-turns", str(max_turns),
        ]
        if mcp_config_path:
            cmd += ["--mcp-config", mcp_config_path]
        if model:
            cmd += ["--model", model]
        return cmd, prompt
    if provider == "agy_cli":
        # agy has no --mcp-config / --allowedTools / --max-turns; MCP is pre-registered.
        cmd = [
            executable, "--dangerously-skip-permissions",
            f"--print={prompt}", "--output-format", "text",
            "--print-timeout", f"{timeout}s",
        ]
        if model:
            cmd += ["--model", model]
        return cmd, ""
    if provider == "gemini_cli":
        env.setdefault("GEMINI_SANDBOX", "false")
        return [executable, "--prompt", "", "--output-format", "text"], prompt
    raise ValueError(f"unsupported model_provider {provider!r}")


def _as_obj(line: str) -> Optional[dict]:
    try:
        v = json.loads(line)
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


def _hit_max_turns(stdout: str) -> bool:
    if '"subtype":"error_max_turns"' in stdout.replace(" ", ""):
        return True
    for line in stdout.splitlines():
        obj = _as_obj(line.strip())
        if obj and obj.get("type") == "result" and obj.get("subtype") == "error_max_turns":
            return True
    return False


def _print_progress(obj: dict, start: float) -> None:
    t = obj.get("type")
    if t == "assistant":
        content = obj.get("message", {}).get("content", [])
        for c in content:
            if c.get("type") == "text" and c.get("text", "").strip():
                print(f"   · {c['text'].strip().splitlines()[0][:100]}")
                break
        for c in content:
            if c.get("type") == "tool_use":
                name = c.get("name", "").split("__")[-1]
                inp = c.get("input", {})
                detail = inp.get("query_path") or inp.get("file_uri") or inp.get("file_path") or ""
                print(f"   · tool {name} {('(' + Path(detail).name + ')') if detail else ''}".rstrip())
    elif t == "result":
        cost = obj.get("total_cost_usd", 0) or 0
        turns = obj.get("num_turns", "?")
        print(f"   done: {turns} turns  ${cost:.4f}  {time.time() - start:.0f}s  status={obj.get('subtype')}")


def _kill_tree(proc: subprocess.Popen) -> None:
    """SIGTERM then SIGKILL the whole process group (agy spawns node + codeql children)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def run_agent_session(
    *,
    cmd: List[str],
    stdin_text: str,
    cwd: Path,
    env: Dict[str, str],
    log_path: Path,
    timeout: int,
    provider: str,
) -> SessionResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(cwd), env=env, start_new_session=True,
    )
    if proc.stdin:
        try:
            proc.stdin.write(stdin_text.encode())
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    stdout_bytes = b""
    stderr_bytes = b""
    start = time.time()
    timed_out = False
    open_streams = [s for s in (proc.stdout, proc.stderr) if s]

    while open_streams:
        if time.time() - start > timeout:
            timed_out = True
            break
        ready, _, _ = select.select(open_streams, [], [], 1.0)
        if not ready:
            if proc.poll() is not None:
                break
            continue
        for stream in ready:
            chunk = stream.readline()
            if not chunk:
                open_streams.remove(stream)
                continue
            if stream is proc.stdout:
                stdout_bytes += chunk
                obj = _as_obj(chunk.decode("utf-8", errors="replace").strip())
                if obj:
                    _print_progress(obj, start)
            else:
                stderr_bytes += chunk

    _kill_tree(proc)

    # group is dead now, so these reads cannot block on inherited pipes
    for stream, sink in ((proc.stdout, "out"), (proc.stderr, "err")):
        if not stream:
            continue
        try:
            rest = stream.read() or b""
        except (OSError, ValueError):
            rest = b""
        if sink == "out":
            stdout_bytes += rest
        else:
            stderr_bytes += rest

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    log_path.write_text(stdout, encoding="utf-8")
    stderr_path = log_path.with_suffix(".stderr.txt")
    stderr_path.write_text(stderr, encoding="utf-8")

    return SessionResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
        hit_max_turns=_hit_max_turns(stdout),
        elapsed_seconds=round(time.time() - start, 1),
        stdout=stdout,
        log_path=str(log_path),
        stderr_path=str(stderr_path),
    )


def extract_last_ql_block(stdout: str) -> Optional[str]:
    """gemini_cli has no Write tool — recover a ```ql block from its text output."""
    blocks = re.findall(r"```(?:ql|codeql)?\s*\n(.*?)```", stdout, re.DOTALL)
    return blocks[-1].strip() if blocks else None


def _extract_text_result(raw: str) -> str:
    """
    Recover the model's answer from a --print invocation, whichever shape the CLI used:
    a single JSON object with a "result" field, an NDJSON stream (take the last "result"
    event), or plain text (agy_cli / gemini_cli).
    """
    raw = raw.strip()
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "result" in obj:
            return str(obj["result"]).strip()
    except json.JSONDecodeError:
        pass
    last_result = None
    for line in raw.splitlines():
        obj = _as_obj(line.strip())
        if obj and obj.get("type") == "result" and "result" in obj:
            last_result = obj["result"]
    if last_result is not None:
        return str(last_result).strip()
    return raw


def run_text_completion(
    *, provider: str, prompt: str, model: Optional[str] = None, timeout: int = 300,
) -> str:
    """
    A one-shot, non-agentic text completion: no MCP server, no tool use, no file writes —
    just "send this prompt, get text back". Used by the DSL-harness extractor, the
    vulnerability-analysis step, and the generalization pass, none of which need the agent
    to touch the filesystem.
    """
    executable, cmd_name = resolve_executable(provider)
    env = os.environ.copy()

    if provider in ("claude_cli", "claude"):
        cmd = [executable, "--print", "--output-format", "json"]
        if model:
            cmd += ["--model", model]
        stdin_text = prompt
    elif provider == "agy_cli":
        cmd = [
            executable, "--dangerously-skip-permissions", f"--print={prompt}",
            "--output-format", "text", "--print-timeout", f"{timeout}s",
        ]
        if model:
            cmd += ["--model", model]
        stdin_text = ""
    elif provider == "gemini_cli":
        env.setdefault("GEMINI_SANDBOX", "false")
        cmd = [executable, "--prompt", "", "--output-format", "text"]
        stdin_text = prompt
    else:
        raise ValueError(f"unsupported model_provider {provider!r}")

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, start_new_session=True,
    )
    try:
        out, err = proc.communicate(input=stdin_text.encode(), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raise RuntimeError(f"{cmd_name} text completion timed out after {timeout}s")

    if proc.returncode != 0 and not out.strip():
        raise RuntimeError(
            f"{cmd_name} text completion failed (rc={proc.returncode}): "
            f"{err.decode('utf-8', errors='replace')[:800]}"
        )
    return _extract_text_result(out.decode("utf-8", errors="replace"))
