#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${ROOT_DIR}/resume}"
BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/.build/resume-assets}"
PROJECT_PYTHON="${ROOT_DIR}/.venv/bin/python"
CODEX_PYTHON="${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"

has_required_python_deps() {
  "$1" - <<'PY' >/dev/null 2>&1
import docx
import reportlab
import yaml
import babel.dates
PY
}

if ! has_required_python_deps "$PYTHON_BIN"; then
  if [[ -x "$PROJECT_PYTHON" ]] && has_required_python_deps "$PROJECT_PYTHON"; then
    PYTHON_BIN="$PROJECT_PYTHON"
  elif [[ -x "$CODEX_PYTHON" ]] && has_required_python_deps "$CODEX_PYTHON"; then
    PYTHON_BIN="$CODEX_PYTHON"
  else
    cat >&2 <<'EOF'
Missing Python dependencies for resume generation.
Install them with:
  python3 -m pip install babel python-docx reportlab pyyaml
EOF
    exit 1
  fi
fi

"$PYTHON_BIN" "${ROOT_DIR}/scripts/generate_resume_outputs.py" \
  --data "${ROOT_DIR}/resume/resume.yaml" \
  --output-root "${OUTPUT_ROOT}" \
  --build-dir "${BUILD_DIR}"
