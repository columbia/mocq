"""One-phase query synthesis: adapt a seed query, score it on the benchmark, iterate."""

from .loop import synthesize

__all__ = ["synthesize"]
