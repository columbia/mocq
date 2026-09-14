# MoCQ

MoCQ is an LLM-driven tool for automatically learning
vulnerability-detection queries for static-analysis frameworks. Given a vulnerability class,
vulnerable/safe examples, and a seed query, MoCQ generates, validates, refines, and evaluates
executable queries.

## Setup

Requirements: Python 3.10+, Node.js 18+, Java 21+ for Java/Joern, PHP CLI for Joern PHP, and an
authenticated CLI agent such as Claude Code, `agy`, or Gemini CLI.

```bash
git clone git@github.com:columbia/mocq.git
cd mocq
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
bash setup.sh
```

The setup script installs CodeQL, the CodeQL libraries, the CodeQL LSP-MCP bridge, and Joern,
then writes the required paths to `.env`.

For Joern, MoCQ also records an engine-observable runtime trace for each query's source, sink,
sanitizer, and data-flow blocks; this trace is used to guide refinement on missed examples.

For Claude Code:

```bash
npm install -g @anthropic-ai/claude-code
claude auth login
```

## Usage

Configurations are in `configs/`; sample benchmarks are in `corpus/`.

CodeQL:

```bash
python -m agent.entry \
  --config configs/CWE-089_sqli.json \
  --engine codeql \
  --languages python,javascript,java
```

Joern:

```bash
python -m agent.entry \
  --config configs/CWE-089_sqli.json \
  --engine joern \
  --languages php
```

MoCQ generates queries for vulnerable examples, merges accepted queries, generalizes
over-specific patterns, removes false positives using safe examples, and keeps the best-F1
result. Results are written to `runs/<config-name>/`; the final query is under `final/`, and
`summary.json` contains metrics and synthesis history.

Useful options are `--max-per-example-attempts N`, `--max-fp-rounds N`, `--no-detection-plan`,
`--no-slicing`, `--no-generalization`, and `--model-provider claude_cli|agy_cli|gemini_cli`.

## Benchmark format

```text
corpus/CWE-089/python/
  vuln/
  safe/
  manifest.json
```

The manifest records the source root and labels each file `vulnerable` or `safe`. Import an
external dataset with:

```bash
python -m agent.benchmark.adapters.generic_dir \
  --src /path/to/dataset/CWE-078 \
  --cwe CWE-078 \
  --lang javascript
```

## Testing

```bash
PYTHONPATH=. python -m pytest -q
```

## Citation

```bibtex
@inproceedings{li2026mocq,
  title     = {Automatically Learning Vulnerability Patterns for Scalable Static Analysis of Web Applications},
  author    = {Penghui Li and Songchen Yao and Josef Sarfati Korich and Changhua Luo and Jianjia Yu and Yinzhi Cao and Junfeng Yang},
  booktitle = {Proceedings of the 33rd ACM SIGSAC Conference on Computer and Communications Security},
  year      = {2026},
  doi       = {10.1145/3830454.3846556}
}
```
