"""
Config loader.

Supports ${VAR} substitution in JSON values using environment variables.
Automatically loads a .env file from the project root if present, so users
only need to run setup.sh once - no manual export required.

Supported variables (set by setup.sh or .env):
    CODEQL_CMD          path to the codeql binary  -> synth_agent.tool_command
    CODEQL_SEARCH_PATH  path passed to CodeQL --search-path / LSP lookup
    CODEQL_SRC          optional codeql repo checkout used to resolve seed_query paths
    JOERN_HOME          dir with joern / joern-parse  -> synth_agent.joern_home (engine: joern)
    JOERN_SRC           Joern source checkout (for agent.dslgen.extract; distinct from JOERN_HOME)
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Optional


def _find_gemini_settings_file(start_dir: str = None) -> Optional[Path]:
    """Find the nearest project .gemini/settings.json, else fall back to user settings."""
    search_dir = Path(start_dir or os.getcwd()).resolve()
    for candidate in [search_dir, *search_dir.parents]:
        settings_path = candidate / ".gemini" / "settings.json"
        if settings_path.exists():
            return settings_path

    user_settings = Path.home() / ".gemini" / "settings.json"
    if user_settings.exists():
        return user_settings
    return None


def load_gemini_model_from_settings(start_dir: str = None) -> Optional[str]:
    """Load Gemini CLI model.name from project/user settings.json if present."""
    settings_path = _find_gemini_settings_file(start_dir)
    if not settings_path:
        return None

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    model = settings.get("model", {})
    if isinstance(model, dict):
        name = model.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


# ---------------------------------------------------------------------------
# .env loader (no third-party dependency)
# ---------------------------------------------------------------------------

def _load_dotenv(start_dir: str = None) -> None:
    """
    Walk up from start_dir (default: cwd) looking for a .env file and load
    its KEY=VALUE pairs into os.environ (existing vars are NOT overwritten).
    """
    search_dir = Path(start_dir or os.getcwd()).resolve()
    for candidate in [search_dir, *search_dir.parents]:
        env_file = candidate / ".env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        os.environ.setdefault(key, value)
            return  # stop at the first .env found


# ---------------------------------------------------------------------------
# ${VAR} substitution
# ---------------------------------------------------------------------------

def _substitute_env_vars(text: str) -> str:
    """
    Replace every ${VAR_NAME} occurrence with os.environ[VAR_NAME].
    Prints a warning for any unset variable so misconfiguration is visible.
    """
    def _replace(match: re.Match) -> str:
        var = match.group(1)
        value = os.environ.get(var)
        if value is None:
            # Legacy configs still mention CODEQL_HOME, but runtime CodeQL paths
            # are now sourced directly from .env overrides below.
            if var == "CODEQL_HOME":
                return match.group(0)
            print(
                f"WARNING: Config references ${{{var}}} but that variable is not set. "
                f"Run setup.sh or export {var} before starting."
            )
            return match.group(0)  # leave the placeholder so the error is obvious
        return value

    return re.sub(r'\$\{([^}]+)\}', _replace, text)


# ---------------------------------------------------------------------------
# CodeQL path overrides
# ---------------------------------------------------------------------------

def _get_env(name: str) -> Optional[str]:
    """Read an env var after ensuring the project .env has been loaded."""
    _load_dotenv()
    value = os.environ.get(name)
    return value.strip() if value else None


def _resolve_codeql_repo_path(path: Optional[str]) -> Optional[str]:
    """
    Repoint any config path that targets the codeql repo checkout so it uses
    CODEQL_SRC from .env instead of relying on configs/*.json path templates.
    """
    if not path:
        return path

    codeql_src = _get_env("CODEQL_SRC")
    if not codeql_src or "codeql-repo" not in path:
        return path

    _, _, suffix = path.partition("codeql-repo")
    suffix = suffix.lstrip("/\\")
    if not suffix:
        return codeql_src
    return str(Path(codeql_src) / suffix)


def _with_codeql_src_search_path(search_path: Optional[str]) -> Optional[str]:
    """
    Ensure generated query packs can resolve CodeQL source packs directly.

    The bundled CodeQL distribution in this environment lives under /codeql,
    whose installation root is /. If a repair-time qlpack cannot resolve
    codeql/cpp-all from the source checkout first, CodeQL may fall back to
    walking the installation root and trip over virtual files under /proc.
    """
    codeql_src = _get_env("CODEQL_SRC")
    if not codeql_src or not Path(codeql_src).exists():
        return search_path

    entries = [
        entry for entry in (search_path or "").split(os.pathsep)
        if entry
    ]
    codeql_src = str(Path(codeql_src))
    if codeql_src not in entries:
        entries.insert(0, codeql_src)
    return os.pathsep.join(entries)


def _apply_engine_env_overrides(config: Any) -> Any:
    """
    Inject tool paths from .env into synth_agent. Each engine's fields are
    independent, so all available ones are filled in regardless of the config's
    ``engine`` (which the CLI can still override). seed_query.codeql.* paths are
    resolved against the CodeQL repo checkout.
    """
    if not isinstance(config, dict):
        return config

    section = config.get("synth_agent")
    if not isinstance(section, dict):
        section = {}
        config["synth_agent"] = section

    # CodeQL
    codeql_cmd = _get_env("CODEQL_CMD")
    codeql_search_path = _get_env("CODEQL_SEARCH_PATH")
    if codeql_cmd and not section.get("tool_command"):
        section["tool_command"] = codeql_cmd
    if not section.get("tool_search_path"):
        if codeql_search_path:
            section["tool_search_path"] = _with_codeql_src_search_path(codeql_search_path)
    else:
        section["tool_search_path"] = _with_codeql_src_search_path(
            _resolve_codeql_repo_path(section["tool_search_path"])
        )
    by_lang = (config.get("seed_query") or {}).get("codeql")
    if isinstance(by_lang, dict):
        for lang, path in by_lang.items():
            by_lang[lang] = _resolve_codeql_repo_path(path)

    # Joern
    joern_home = _get_env("JOERN_HOME")
    if joern_home and not section.get("joern_home"):
        section["joern_home"] = joern_home

    return config


# backwards-compatible alias
_apply_codeql_env_overrides = _apply_engine_env_overrides


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(config_file: str) -> dict:
    """
    Load a JSON config file, substituting ${VAR} placeholders with environment
    variables. A .env file in the project root is loaded automatically.
    """
    _load_dotenv()

    with open(config_file, "r") as f:
        raw = f.read()

    substituted = _substitute_env_vars(raw)

    try:
        config = json.loads(substituted)
        return _apply_codeql_env_overrides(config)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Config file {config_file} is not valid JSON after variable "
            f"substitution.\n  Error: {e}\n  Hint: check that all ${{VAR}} "
            f"placeholders are set in your .env file."
        ) from e
