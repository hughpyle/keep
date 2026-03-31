#!/usr/bin/env bash
# Check dependency licenses for GPL/AGPL/LGPL violations.
# Usage: ./scripts/check_licenses.sh
set -euo pipefail

DENY="GNU General Public License v2 (GPLv2);\
GNU General Public License v3 (GPLv3);\
GNU General Public License v2 or later (GPLv2+);\
GNU General Public License v3 or later (GPLv3+);\
GNU Affero General Public License v3 (AGPLv3);\
GNU Affero General Public License v3 or later (AGPLv3+);\
GNU Lesser General Public License v2 (LGPLv2);\
GNU Lesser General Public License v2 or later (LGPLv2+);\
GNU Lesser General Public License v3 (LGPLv3);\
GNU Lesser General Public License v3 or later (LGPLv3+)"

# pip-licenses needs --python to point at keep's interpreter so it can read
# the installed package metadata.  uv tool run provides the pip-licenses binary
# while --python tells it which site-packages to inspect.
KEEP_PYTHON="$HOME/.local/share/uv/tools/keep-skill/bin/python"
run_licenses() {
    if [ -x "$KEEP_PYTHON" ]; then
        uv tool run pip-licenses --python="$KEEP_PYTHON" "$@"
    elif command -v uv &>/dev/null; then
        uv run pip-licenses "$@"
    else
        pip-licenses "$@"
    fi
}

echo "=== Dependency License Check ==="
run_licenses --fail-on="$DENY" --format=plain --order=license

# Warn about UNKNOWN licenses
UNKNOWN=$(run_licenses --format=csv | grep 'UNKNOWN' || true)
if [ -n "$UNKNOWN" ]; then
    echo ""
    echo "WARNING: Packages with UNKNOWN license metadata (verify manually):"
    echo "$UNKNOWN"
fi

echo ""
echo "All licenses OK."
