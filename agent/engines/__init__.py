"""Query-engine backends. CodeQL (Python/JS/Java) and Joern (adds PHP)."""

from pathlib import Path
from typing import Optional

from .base import DbResult, QueryEngine, ValidateResult


def get_engine(
    name: str,
    *,
    tool_command: str = "codeql",
    search_path: str = "",
    lsp_mcp_path: Optional[str] = None,
    joern_home: str = "",
) -> QueryEngine:
    if name == "codeql":
        from .codeql import CodeQLEngine

        if lsp_mcp_path is None:
            lsp_mcp_path = str(
                Path(__file__).resolve().parents[2] / "codeql-lsp-mcp" / "dist" / "index.js"
            )
        return CodeQLEngine(
            tool_command=tool_command, search_path=search_path, lsp_mcp_path=lsp_mcp_path,
        )
    if name == "joern":
        from .joern import JoernEngine

        return JoernEngine(joern_home=joern_home)
    raise ValueError(f"unknown engine {name!r} (supported: 'codeql', 'joern')")


__all__ = ["QueryEngine", "DbResult", "ValidateResult", "get_engine"]
