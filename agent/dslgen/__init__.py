"""
DSL Harness Extraction + Subsetting (MoCQ paper §3.1.1 / §3.1.2).

Given an engine's DSL surface (Joern's CPGQL today), automatically derive the compact
harness fed to the synthesis agent — instead of hand-authoring it — by scanning the
engine's own source for public step definitions, best-effort-fetching its public docs,
having an LLM turn that raw material into a structured API catalog + grammar skeleton,
then having a second LLM pass select the compact, high-expressivity subset actually
needed for vulnerability-detection queries.

Run once per engine, reused across every CWE config, exactly like the paper's "once per
tool" DSL harness:

    python -m agent.dslgen.extract --engine joern --joern-src ~/mocq/joern
"""
