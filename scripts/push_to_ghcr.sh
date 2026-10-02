#!/usr/bin/env bash
set -euo pipefail

# The historical implementation published every *_arm64.sif in a directory and
# treated tag existence as artifact identity. That behavior is intentionally
# disabled. This compatibility entry point now performs identity evaluation
# only; a separately reviewed publisher must consume PUBLISH_NEW decisions.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/sif_identity.py" "$@"
