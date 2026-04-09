#!/usr/bin/env bash
#
# Install the Keep memory provider plugin into Hermes Agent.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/keepnotes-ai/keep/main/scripts/install-hermes-plugin.sh | bash
#
# Or run locally:
#   bash scripts/install-hermes-plugin.sh
#
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/keepnotes-ai/keep/main"
PLUGIN_FILES=(
    "keep/hermes/plugin/__init__.py"
    "keep/hermes/plugin/cli.py"
    "keep/hermes/plugin/plugin.yaml"
    "keep/hermes/plugin/README.md"
)

info()  { printf '\033[0;36m%s\033[0m\n' "$*"; }
ok()    { printf '\033[0;32m✓ %s\033[0m\n' "$*"; }
warn()  { printf '\033[0;33m⚠ %s\033[0m\n' "$*"; }
err()   { printf '\033[0;31m✗ %s\033[0m\n' "$*" >&2; }
die()   { err "$@"; exit 1; }

# --- Locate Hermes -----------------------------------------------------------

if ! command -v hermes &>/dev/null; then
    die "hermes is not installed or not in PATH.
    Install: https://github.com/NousResearch/hermes-agent#installation"
fi

HERMES_ROOT=$(hermes config show 2>/dev/null | grep "Install:" | awk '{print $2}')
if [ -z "$HERMES_ROOT" ]; then
    die "Could not determine Hermes install path from 'hermes config show'."
fi

PLUGIN_DIR="$HERMES_ROOT/plugins/memory/keep"
if [ ! -d "$HERMES_ROOT/plugins/memory" ]; then
    die "plugins/memory/ not found at $HERMES_ROOT — is this a valid Hermes installation?"
fi

# --- Source Hermes env for API key detection ----------------------------------

ENV_PATH=$(hermes config env-path 2>/dev/null || true)
if [ -n "$ENV_PATH" ] && [ -f "$ENV_PATH" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_PATH"
    set +a
fi

# --- Show plan and confirm ----------------------------------------------------

echo ""
info "Keep Memory Provider — Hermes Plugin Installer"
echo ""
echo "  Hermes install:  $HERMES_ROOT"
echo "  Plugin target:   $PLUGIN_DIR"
echo ""

# Show which API keys are available (affects provider detection in setup)
HAS_KEYS=""
for key in OPENAI_API_KEY OPENROUTER_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY VOYAGE_API_KEY MISTRAL_API_KEY; do
    val="${!key:-}"
    if [ -n "$val" ]; then
        masked="...${val: -4}"
        echo "  $key  $masked"
        HAS_KEYS="yes"
    fi
done

# Check for Ollama
if command -v ollama &>/dev/null || curl -sf http://localhost:11434/api/tags &>/dev/null; then
    echo "  Ollama           available"
    HAS_KEYS="yes"
fi

if [ -z "$HAS_KEYS" ]; then
    echo "  No API keys or Ollama detected."
    echo "  Keep needs at least one embedding provider."
    echo "  Set an API key in $ENV_PATH or install Ollama first."
fi
echo ""

if [ -d "$PLUGIN_DIR" ]; then
    warn "Plugin directory already exists — files will be overwritten."
    echo ""
fi

read -rp "Install Keep plugin and run setup? [Y/n] " REPLY
REPLY=${REPLY:-Y}
if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# --- Download and install plugin files ----------------------------------------

info "Downloading plugin files..."
mkdir -p "$PLUGIN_DIR"

for file in "${PLUGIN_FILES[@]}"; do
    dest_name=$(basename "$file")
    if ! curl -sSfL "$REPO_RAW/$file" -o "$PLUGIN_DIR/$dest_name"; then
        die "Failed to download $file"
    fi
done

ok "Plugin installed to $PLUGIN_DIR"
echo ""

# --- Run hermes memory setup --------------------------------------------------

info "Running hermes memory setup..."
echo "  Select 'keep' when prompted, then choose your providers."
echo ""

hermes memory setup

echo ""
ok "Done! Start a new Hermes session to use Keep memory."
