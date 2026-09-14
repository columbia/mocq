"""
Engine-neutral run specification for one-phase web-query synthesis.

A config JSON describes a vulnerability class, which engine to target, the
per-engine/per-language seed query to adapt, and the labelled benchmark
manifest to score against. Nothing here is CodeQL-specific — a Joern backend
reuses the same shapes with ``engine: "joern"`` and ``seed_query.joern.*``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

SUPPORTED_LANGUAGES = ("python", "javascript", "java", "php")


@dataclass
class VulnSpec:
    id: str                 # e.g. "CWE-089"
    name: str               # e.g. "SQL Injection"
    description: str
    languages: List[str]    # subset of SUPPORTED_LANGUAGES

    @classmethod
    def from_dict(cls, d: dict) -> "VulnSpec":
        langs = [l.lower() for l in d["languages"]]
        unknown = [l for l in langs if l not in SUPPORTED_LANGUAGES]
        if unknown:
            raise ValueError(f"unsupported language(s) {unknown}; supported: {SUPPORTED_LANGUAGES}")
        return cls(
            id=d["id"],
            name=d["name"],
            description=d.get("description", ""),
            languages=langs,
        )


@dataclass
class SynthAgentConfig:
    model: Optional[str] = None
    model_provider: str = "claude_cli"  # claude_cli | agy_cli | gemini_cli
    cli_max_turns: int = 20
    cli_timeout: int = 1800
    tool_command: str = "codeql"       # codeql engine
    tool_search_path: str = ""         # codeql engine
    joern_home: str = ""               # joern engine (dir with joern / joern-parse)
    prompts_dir: str = "agent/prompts"
    api_key: str = ""
    max_tokens: int = 8000
    temperature: float = 0.1
    #: MoCQ Algorithm 1 MaxN — refinement attempts per vulnerable example
    max_per_example_attempts: int = 5
    #: false-positive elimination rounds against the safe set
    max_fp_refine_rounds: int = 3
    #: MoCQ §3.2.1 — CoT detection plan per example, folded into the per-example prompt
    enable_detection_plan: bool = True
    #: MoCQ §3.2 — program slice per example (Joern only; no-op for engines without slice_file)
    enable_slicing: bool = True
    #: MoCQ §3.3.2 — detect + fix overfitting after merge, before FP-elimination
    enable_generalization: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "SynthAgentConfig":
        d = d or {}
        return cls(
            model=d.get("model"),
            model_provider=d.get("model_provider", "claude_cli"),
            cli_max_turns=d.get("cli_max_turns", 20),
            cli_timeout=d.get("cli_timeout", 1800),
            tool_command=d.get("tool_command", "codeql"),
            tool_search_path=d.get("tool_search_path", ""),
            joern_home=d.get("joern_home", ""),
            prompts_dir=d.get("prompts_dir", "agent/prompts"),
            api_key=d.get("api_key", ""),
            max_tokens=d.get("max_tokens", 8000),
            temperature=d.get("temperature", 0.1),
            max_per_example_attempts=d.get("max_per_example_attempts", 5),
            max_fp_refine_rounds=d.get("max_fp_refine_rounds", 3),
            enable_detection_plan=d.get("enable_detection_plan", True),
            enable_slicing=d.get("enable_slicing", True),
            enable_generalization=d.get("enable_generalization", True),
        )


@dataclass
class RunSpec:
    vuln: VulnSpec
    engine: str                                  # "codeql"
    seed_query: Dict[str, Dict[str, str]]        # engine -> language -> query path
    benchmark: Dict[str, str]                    # language -> manifest.json path
    synth_agent: SynthAgentConfig
    config_path: str = ""

    @property
    def stem(self) -> str:
        return Path(self.config_path).stem or self.vuln.id

    def seed_for(self, language: str) -> str:
        try:
            return self.seed_query[self.engine][language]
        except KeyError as exc:
            raise KeyError(
                f"config has no seed_query for engine={self.engine!r} language={language!r}"
            ) from exc

    def manifest_for(self, language: str) -> str:
        if language not in self.benchmark:
            raise KeyError(f"config has no benchmark manifest for language={language!r}")
        return self.benchmark[language]

    @classmethod
    def from_dict(cls, d: dict, config_path: str = "") -> "RunSpec":
        return cls(
            vuln=VulnSpec.from_dict(d["vuln"]),
            engine=d.get("engine", "codeql"),
            seed_query=d["seed_query"],
            benchmark=d["benchmark"],
            synth_agent=SynthAgentConfig.from_dict(d.get("synth_agent", {})),
            config_path=config_path,
        )
