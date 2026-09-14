"""
Generic corpus adapter.

Given an external directory that separates known-vulnerable from known-safe
sources, materialise a ``corpus/<CWE>/<lang>/`` tree plus a ``manifest.json``
the benchmark runner understands.

Expected input layout (either form works):

    <src>/vulnerable/**    <src>/safe/**
    <src>/vuln/**          <src>/benign/**

Usage:

    python -m agent.benchmark.adapters.generic_dir \\
        --src datasets/secbenchjs/CWE-078 --cwe CWE-078 --lang javascript \\
        --dest corpus

Only files with an extension matching ``--lang`` are copied.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import List, Tuple

_LANG_EXT = {
    "python": {".py"},
    "javascript": {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"},
    "java": {".java"},
    "php": {".php", ".phtml", ".inc"},
}
_VULN_DIRS = {"vulnerable", "vuln", "vuln_cases", "bad", "positive", "positives"}
_SAFE_DIRS = {"safe", "benign", "good", "negative", "negatives", "ok"}


def _classify_top(name: str) -> str | None:
    n = name.lower()
    if n in _VULN_DIRS:
        return "vulnerable"
    if n in _SAFE_DIRS:
        return "safe"
    return None


def _collect(src: Path, lang: str) -> List[Tuple[Path, str]]:
    exts = _LANG_EXT[lang]
    out: List[Tuple[Path, str]] = []
    for top in sorted(src.iterdir()):
        if not top.is_dir():
            continue
        label = _classify_top(top.name)
        if label is None:
            continue
        for path in sorted(top.rglob("*")):
            if path.is_file() and path.suffix.lower() in exts:
                out.append((path, label))
    return out


def build(src: Path, cwe: str, lang: str, dest_root: Path) -> Path:
    if lang not in _LANG_EXT:
        raise SystemExit(f"--lang must be one of {sorted(_LANG_EXT)}")
    src = src.resolve()
    if not src.is_dir():
        raise SystemExit(f"--src not a directory: {src}")

    collected = _collect(src, lang)
    if not collected:
        raise SystemExit(
            f"no {lang} files found under {src} in vulnerable/ or safe/ subdirectories"
        )

    lang_root = (dest_root / cwe / lang).resolve()
    for sub in ("vuln", "safe"):
        target = lang_root / sub
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

    manifest_files = []
    seen = set()
    for path, label in collected:
        bucket = "vuln" if label == "vulnerable" else "safe"
        rel_name = path.name
        rel = f"{bucket}/{rel_name}"
        i = 1
        while rel in seen:
            rel = f"{bucket}/{path.stem}_{i}{path.suffix}"
            i += 1
        seen.add(rel)
        shutil.copy2(path, lang_root / rel)
        manifest_files.append({"path": rel, "label": label})

    manifest = {
        "cwe": cwe,
        "language": lang,
        "source_root": ".",
        "files": sorted(manifest_files, key=lambda e: e["path"]),
    }
    manifest_path = lang_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    n_vuln = sum(1 for e in manifest_files if e["label"] == "vulnerable")
    n_safe = len(manifest_files) - n_vuln
    print(f"wrote {manifest_path}")
    print(f"  {n_vuln} vulnerable, {n_safe} safe  ({lang})")
    return manifest_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--cwe", required=True, help="e.g. CWE-089")
    ap.add_argument("--lang", required=True, choices=sorted(_LANG_EXT))
    ap.add_argument("--dest", type=Path, default=Path("corpus"))
    args = ap.parse_args()
    build(args.src, args.cwe, args.lang, args.dest)


if __name__ == "__main__":
    main()
