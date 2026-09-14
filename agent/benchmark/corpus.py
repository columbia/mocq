"""
Labelled benchmark corpus.

A manifest is a small JSON file describing one (CWE, language) slice:

    {
      "cwe": "CWE-089",
      "language": "python",
      "source_root": "python",
      "files": [
        {"path": "vuln/sqli_01.py", "label": "vulnerable"},
        {"path": "safe/sqli_01_ok.py", "label": "safe"}
      ]
    }

``source_root`` is resolved relative to the manifest file. Every entry in
``files`` is a path under ``source_root``. Labels are ``"vulnerable"`` or
``"safe"``. No line numbers — scoring is per file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

VULNERABLE = "vulnerable"
SAFE = "safe"
_VALID_LABELS = {VULNERABLE, SAFE}


@dataclass(frozen=True)
class CorpusFile:
    path: str      # POSIX, relative to Manifest.source_root
    label: str     # VULNERABLE | SAFE


@dataclass
class Manifest:
    cwe: str
    language: str
    source_root: Path          # resolved, absolute
    files: List[CorpusFile]
    manifest_path: Path

    @property
    def vulnerable(self) -> List[str]:
        return [f.path for f in self.files if f.label == VULNERABLE]

    @property
    def safe(self) -> List[str]:
        return [f.path for f in self.files if f.label == SAFE]

    def label_of(self, rel_path: str) -> str | None:
        key = _norm(rel_path)
        for f in self.files:
            if _norm(f.path) == key:
                return f.label
        return None

    def fingerprint(self) -> str:
        """Content hash over the actual files, so a rebuilt DB is cache-keyed."""
        h = hashlib.sha256()
        h.update(self.cwe.encode())
        h.update(self.language.encode())
        for f in sorted(self.files, key=lambda c: c.path):
            abs_path = self.source_root / f.path
            h.update(f.path.encode())
            h.update(f.label.encode())
            try:
                h.update(abs_path.read_bytes())
            except OSError:
                h.update(b"<missing>")
        return h.hexdigest()

    def missing_files(self) -> List[str]:
        return [f.path for f in self.files if not (self.source_root / f.path).is_file()]


def _norm(p: str) -> str:
    return Path(p).as_posix().lstrip("./")


def load_manifest(manifest_path: str | Path) -> Manifest:
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"benchmark manifest not found: {manifest_path}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_root = data.get("source_root", ".")
    source_root = Path(raw_root)
    if not source_root.is_absolute():
        source_root = (manifest_path.parent / source_root).resolve()

    files: List[CorpusFile] = []
    for entry in data.get("files", []):
        label = entry["label"].lower()
        if label not in _VALID_LABELS:
            raise ValueError(
                f"{manifest_path}: file {entry.get('path')!r} has label {label!r}; "
                f"expected one of {sorted(_VALID_LABELS)}"
            )
        files.append(CorpusFile(path=_norm(entry["path"]), label=label))

    if not files:
        raise ValueError(f"{manifest_path}: manifest lists no files")

    manifest = Manifest(
        cwe=data["cwe"],
        language=data["language"].lower(),
        source_root=source_root,
        files=files,
        manifest_path=manifest_path,
    )
    missing = manifest.missing_files()
    if missing:
        raise FileNotFoundError(
            f"{manifest_path}: {len(missing)} listed file(s) missing under {source_root}: "
            + ", ".join(missing[:5])
            + (" ..." if len(missing) > 5 else "")
        )
    return manifest
