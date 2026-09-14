"""
Build the benchmark database (cached) and score a query's output against the
manifest labels, per file.

  vulnerable file, flagged      -> true positive
  vulnerable file, not flagged  -> false negative
  safe file, flagged            -> false positive
  safe file, not flagged        -> true negative

Findings in files not listed in the manifest are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

from ..engines.base import QueryEngine
from .corpus import Manifest, _norm


@dataclass
class Metrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def accuracy(self) -> float:
        denom = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.tn) / denom if denom else 0.0

    def as_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "accuracy": round(self.accuracy, 4),
        }


@dataclass
class ScoreResult:
    metrics: Metrics
    caught: List[str] = field(default_factory=list)        # vulnerable & flagged
    missed: List[str] = field(default_factory=list)        # vulnerable & not flagged
    false_alarms: List[str] = field(default_factory=list)  # safe & flagged

    def as_dict(self) -> dict:
        return {
            "metrics": self.metrics.as_dict(),
            "caught": self.caught,
            "missed": self.missed,
            "false_alarms": self.false_alarms,
        }


def score_per_file(flagged: Set[str], manifest: Manifest) -> ScoreResult:
    flagged_norm = {_norm(p) for p in flagged}
    m = Metrics()
    caught, missed, false_alarms = [], [], []

    for f in manifest.files:
        hit = _norm(f.path) in flagged_norm
        if f.label == "vulnerable":
            if hit:
                m.tp += 1
                caught.append(f.path)
            else:
                m.fn += 1
                missed.append(f.path)
        else:  # safe
            if hit:
                m.fp += 1
                false_alarms.append(f.path)
            else:
                m.tn += 1

    return ScoreResult(metrics=m, caught=caught, missed=missed, false_alarms=false_alarms)


def build_or_get_db(
    manifest: Manifest,
    engine: QueryEngine,
    cache_dir: Optional[Path] = None,
    *,
    force: bool = False,
) -> Path:
    cache_dir = Path(cache_dir) if cache_dir else (manifest.manifest_path.parent / ".dbcache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    fp = manifest.fingerprint()[:16]
    db_path = cache_dir / f"{engine.name}-{manifest.language}-{fp}"
    ok_marker = db_path.with_suffix(".ok")

    if db_path.is_dir() and ok_marker.exists() and not force:
        return db_path

    result = engine.build_database(manifest.source_root, manifest.language, db_path)
    if not result.ok:
        raise RuntimeError(
            f"failed to build {engine.name} database for {manifest.cwe}/{manifest.language}:\n"
            f"{result.error}"
        )
    ok_marker.write_text(fp, encoding="utf-8")
    return db_path
