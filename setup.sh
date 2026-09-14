#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup.sh — Download CodeQL CLI 2.23.1 and the CodeQL query library
#            into ./codeql-home (inside the project), then write a .env file.
#
# Supports:
#   macOS  — Intel (x86_64) and Apple Silicon (arm64)
#   Linux  — x86_64 and ARM64 (aarch64)
#
# Usage:
#   bash setup.sh                              # installs to ./codeql-home
#   CODEQL_HOME=/custom/path bash setup.sh     # override install location
# ---------------------------------------------------------------------------
set -euo pipefail

CODEQL_VERSION="2.23.1"
CODEQL_REPO_COMMIT="402d58bc3afd899eed45c7041707adab09db0a1f"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEQL_HOME="${CODEQL_HOME:-$SCRIPT_DIR/codeql-home}"
BASE_URL="https://github.com/github/codeql-cli-binaries/releases/download/v${CODEQL_VERSION}"

# ---- colour helpers --------------------------------------------------------
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
red()    { printf '\033[0;31m%s\033[0m\n' "$*"; exit 1; }

# ---- platform detection ----------------------------------------------------
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS-$ARCH" in
  Darwin-x86_64)          PLATFORM="osx64"       ; FALLBACK="" ;;
  Darwin-arm64)           PLATFORM="osx-arm64"   ; FALLBACK="osx64" ;;
  Linux-x86_64)           PLATFORM="linux64"     ; FALLBACK="" ;;
  Linux-aarch64|Linux-arm64) PLATFORM="linux-arm64" ; FALLBACK="" ;;
  *) red "Unsupported platform: $OS-$ARCH" ;;
esac

CLI_DIR="$CODEQL_HOME/codeql"
REPO_DIR="$CODEQL_HOME/codeql-repo"
CLI_BIN="$CLI_DIR/codeql"
ENV_FILE="$SCRIPT_DIR/.env"

green "=== CodeQL setup ==="
echo  "  OS / arch    : $OS / $ARCH"
echo  "  Platform pkg : $PLATFORM"
echo  "  CLI version  : $CODEQL_VERSION"
echo  "  Repo commit  : $CODEQL_REPO_COMMIT"
echo  "  CODEQL_HOME  : $CODEQL_HOME"
echo ""

mkdir -p "$CODEQL_HOME"

# ---------------------------------------------------------------------------
# helper: resolve the best available download URL for this platform
# ---------------------------------------------------------------------------
resolve_url() {
  local primary="$BASE_URL/codeql-${PLATFORM}.zip"

  # A redirect (302) or direct (200) means the asset exists.
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 -I "$primary")

  if [ "$code" = "200" ] || [ "$code" = "302" ]; then
    echo "$primary"
    return
  fi

  if [ -n "$FALLBACK" ]; then
    local fallback_url="$BASE_URL/codeql-${FALLBACK}.zip"
    yellow "Native package for '${PLATFORM}' not found (HTTP $code)." >&2
    yellow "Falling back to '${FALLBACK}' — requires Rosetta 2 on Apple Silicon." >&2
    PLATFORM="$FALLBACK"
    echo "$fallback_url"
    return
  fi

  red "Could not find a CodeQL CLI package for platform '${PLATFORM}' (HTTP $code)." >&2
  exit 1
}

# ---------------------------------------------------------------------------
# 1. CodeQL CLI
# ---------------------------------------------------------------------------
if [ -f "$CLI_BIN" ]; then
  INSTALLED="$("$CLI_BIN" --version 2>/dev/null | head -1 || echo unknown)"
  yellow "CodeQL CLI already present ($INSTALLED) — skipping download"
else
  echo "Resolving download URL for platform '${PLATFORM}'..."
  DOWNLOAD_URL="$(resolve_url)"
  echo "Downloading from: $DOWNLOAD_URL"

  TMP_ZIP="$(mktemp /tmp/codeql-cli-XXXXXX)"
  curl -fSL --progress-bar "$DOWNLOAD_URL" -o "$TMP_ZIP"

  echo "Extracting..."
  unzip -q "$TMP_ZIP" -d "$CODEQL_HOME"
  rm "$TMP_ZIP"
  chmod +x "$CLI_BIN"
  green "✅ CodeQL CLI installed at $CLI_BIN"
fi

# ---------------------------------------------------------------------------
# 2. CodeQL query library (shallow fetch of the exact commit)
# ---------------------------------------------------------------------------
if [ -d "$REPO_DIR/.git" ]; then
  CURRENT="$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
  if [ "$CURRENT" = "$CODEQL_REPO_COMMIT" ]; then
    yellow "CodeQL repo already at $CODEQL_REPO_COMMIT — skipping"
  else
    yellow "CodeQL repo at a different commit ($CURRENT), re-fetching..."
    git -C "$REPO_DIR" fetch --depth 1 origin "$CODEQL_REPO_COMMIT"
    git -C "$REPO_DIR" checkout FETCH_HEAD
    green "✅ CodeQL repo updated to $CODEQL_REPO_COMMIT"
  fi
else
  echo "Cloning CodeQL query library (shallow, single commit — no full history)..."
  mkdir -p "$REPO_DIR"
  git init "$REPO_DIR" -q
  git -C "$REPO_DIR" remote add origin https://github.com/github/codeql.git
  git -C "$REPO_DIR" fetch --depth 1 origin "$CODEQL_REPO_COMMIT"
  git -C "$REPO_DIR" checkout FETCH_HEAD
  green "✅ CodeQL repo cloned to $REPO_DIR"
fi

# ---------------------------------------------------------------------------
# 3. Verify CLI runs
# ---------------------------------------------------------------------------
echo ""
echo "Verifying CLI..."
if "$CLI_BIN" --version > /dev/null 2>&1; then
  green "✅ $("$CLI_BIN" --version | head -1)"
else
  # On macOS ARM with the x86 fallback, Rosetta 2 may not be installed
  if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
    yellow "⚠️  CLI verification failed on Apple Silicon."
    yellow "   If Rosetta 2 is not installed, run: softwareupdate --install-rosetta"
  fi
  red "❌ CodeQL CLI failed to run — check $CLI_BIN"
fi

# ---------------------------------------------------------------------------
# 4. Write .env
# ---------------------------------------------------------------------------
cat > "$ENV_FILE" << EOF
# Auto-generated by setup.sh — re-run setup.sh to regenerate
CODEQL_HOME=$CODEQL_HOME
CODEQL_CMD=$CLI_BIN
CODEQL_SEARCH_PATH=$REPO_DIR
EOF

green "✅ .env written to $ENV_FILE"
echo ""
# ---------------------------------------------------------------------------
# 5. CodeQL LSP MCP server (vendored in this repository)
# ---------------------------------------------------------------------------
MCP_DIR="$SCRIPT_DIR/codeql-lsp-mcp"

if [ ! -f "$MCP_DIR/package.json" ]; then
  red "Vendored codeql-lsp-mcp is missing from $MCP_DIR"
fi

if [ ! -f "$MCP_DIR/dist/index.js" ]; then
  echo "Building CodeQL LSP MCP server..."
  if ! command -v node &>/dev/null; then
    red "Node.js not found — install Node.js (>=18) to build codeql-lsp-mcp"
  fi
  (cd "$MCP_DIR" && npm install --silent && npm run build)
  green "✅ codeql-lsp-mcp built"
else
  yellow "codeql-lsp-mcp already built — skipping"
fi

# ---------------------------------------------------------------------------
# 6. Joern (for the joern engine — Python/JS/Java/PHP via CPG, incl. PHP)
# ---------------------------------------------------------------------------
JOERN_VERSION="v4.0.614"
JOERN_HOME=""
JOERN_SRC=""  # source checkout, only set when we have one (used by agent.dslgen.extract)

# (a0) build the patched fork (MoCQ partial-flow hook) when explicitly requested — large, sbt build
if [ "${JOERN_BUILD_FORK:-0}" = "1" ]; then
  FORK_DIR="$SCRIPT_DIR/joern-src"
  if [ ! -x "$FORK_DIR/joern-parse" ]; then
    echo "Building patched Joern fork (JOERN_BUILD_FORK=1) ..."
    [ -d "$FORK_DIR/.git" ] || git clone --depth 1 -b "$JOERN_VERSION" \
      https://github.com/joernio/joern.git "$FORK_DIR"
    git -C "$FORK_DIR" apply --3way "$SCRIPT_DIR/patches/joern-mocq-partial-flow.patch" || \
      yellow "⚠️  patch did not apply cleanly — inspect patches/joern-mocq-partial-flow.patch"
    (cd "$FORK_DIR" && sbt -batch "joerncli/stage")
    ( cd "$FORK_DIR/joern-cli/target/universal/stage/lib" && \
      for p in dataflowengineoss macros console semanticcpg; do \
        ls -t io.joern.$p-*.jar 2>/dev/null | tail -n +2 | xargs -r rm -f; done )
  fi
  JOERN_HOME="$FORK_DIR"
  JOERN_SRC="$FORK_DIR"
  green "✅ patched Joern fork at $JOERN_HOME (reachableByPartialFlows enabled)"

# (a) reuse an existing install if one is on PATH
elif command -v joern-parse >/dev/null 2>&1; then
  JOERN_HOME="$(dirname "$(command -v joern-parse)")"
  yellow "Using Joern already on PATH: $JOERN_HOME"
# (b) reuse a previous download
elif [ -x "$SCRIPT_DIR/joern-home/joern-cli/joern-parse" ]; then
  JOERN_HOME="$SCRIPT_DIR/joern-home/joern-cli"
  yellow "Joern already present at $JOERN_HOME — skipping"
# (c) download the platform build (large: ~1.7 GB, bundles a JRE + all frontends)
else
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)  JOERN_ASSET="joern-cli-macos-arm64.zip" ;;
    Darwin-x86_64) JOERN_ASSET="joern-cli-macos-x86_64.zip" ;;
    Linux-aarch64|Linux-arm64) JOERN_ASSET="joern-cli-linux-arm64.zip" ;;
    Linux-x86_64)  JOERN_ASSET="joern-cli-linux-x86_64.zip" ;;
    *) JOERN_ASSET="" ;;
  esac
  if [ -n "$JOERN_ASSET" ]; then
    echo "Downloading Joern $JOERN_VERSION ($JOERN_ASSET, ~1.7 GB) ..."
    mkdir -p "$SCRIPT_DIR/joern-home"
    JOERN_ZIP="$(mktemp /tmp/joern-cli-XXXXXX.zip)"
    if curl -fSL --progress-bar \
        "https://github.com/joernio/joern/releases/download/${JOERN_VERSION}/${JOERN_ASSET}" \
        -o "$JOERN_ZIP"; then
      unzip -q -o "$JOERN_ZIP" -d "$SCRIPT_DIR/joern-home"
      rm -f "$JOERN_ZIP"
      chmod +x "$SCRIPT_DIR"/joern-home/joern-cli/joern* 2>/dev/null || true
      JOERN_HOME="$SCRIPT_DIR/joern-home/joern-cli"
      green "✅ Joern installed at $JOERN_HOME"
    else
      yellow "⚠️  Could not download Joern — the 'joern' engine will be unavailable."
    fi
  else
    yellow "⚠️  No Joern build for $(uname -s)-$(uname -m); install joern manually and re-run."
  fi
fi

if ! command -v java >/dev/null 2>&1; then
  yellow "⚠️  'java' not found — Joern and the CodeQL Java path need a JDK (>= 21)."
fi
if [ -n "$JOERN_HOME" ] && ! command -v php >/dev/null 2>&1; then
  yellow "⚠️  'php' not found — Joern's PHP frontend needs it (brew install php / apt-get install php-cli)."
fi

if [ -n "$JOERN_HOME" ]; then
  printf 'JOERN_HOME=%s\n' "$JOERN_HOME" >> "$ENV_FILE"
  green "✅ JOERN_HOME appended to $ENV_FILE"
fi
if [ -n "$JOERN_SRC" ]; then
  printf 'JOERN_SRC=%s\n' "$JOERN_SRC" >> "$ENV_FILE"
  green "✅ JOERN_SRC appended to $ENV_FILE (agent.dslgen.extract: DSL harness extraction)"
fi

green "Setup complete."
echo ""
echo "Synthesize a web query (per-example -> merge -> FP-elimination):"
echo "  python -m agent.entry --config configs/CWE-089_sqli.json \\"
echo "    --languages python,javascript,java"
echo ""
echo "PHP (Joern engine):"
echo "  python -m agent.entry --config configs/CWE-089_sqli.json --engine joern --languages php"
echo ""
echo "Ingest an external labelled dataset into the corpus first:"
echo "  python -m agent.benchmark.adapters.generic_dir \\"
echo "    --src /path/to/dataset/CWE-078 --cwe CWE-078 --lang php"
