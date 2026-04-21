#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PDF_ENGINE="${PDF_ENGINE:-xelatex}"
MAIN_FONT="${MAIN_FONT:-}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${ROOT_DIR}/resume}"
TEMPLATE_DIR="${ROOT_DIR}/resume/templates"
DOCX_REFERENCE="${DOCX_REFERENCE:-${TEMPLATE_DIR}/reference.docx}"
PDF_HEADER="${PDF_HEADER:-${TEMPLATE_DIR}/pdf-header.tex}"

require_binary() {
  local bin_name="$1"
  if ! command -v "$bin_name" >/dev/null 2>&1; then
    echo "Required binary '$bin_name' was not found in PATH." >&2
    exit 1
  fi
}

build_resume() {
  local lang="$1"
  local source_file="${ROOT_DIR}/resume/${lang}/resume.md"
  local output_dir="${OUTPUT_ROOT}/${lang}"

  if [[ ! -f "$source_file" ]]; then
    echo "Missing source file: $source_file" >&2
    exit 1
  fi

  mkdir -p "$output_dir"

  pandoc "$source_file" --from=gfm --to=plain -o "${output_dir}/resume.txt"
  pandoc "$source_file" \
    --from=gfm \
    --standalone \
    --reference-doc="$DOCX_REFERENCE" \
    -o "${output_dir}/resume.docx"
  if [[ -n "${MAIN_FONT}" ]]; then
    pandoc "$source_file" \
      --from=gfm \
      --standalone \
      --pdf-engine="$PDF_ENGINE" \
      --include-in-header="$PDF_HEADER" \
      -V "mainfont=${MAIN_FONT}" \
      -V geometry:margin=0.7in \
      -V fontsize=11pt \
      -V linestretch=1.06 \
      -o "${output_dir}/resume.pdf"
  else
    pandoc "$source_file" \
      --from=gfm \
      --standalone \
      --pdf-engine="$PDF_ENGINE" \
      --include-in-header="$PDF_HEADER" \
      -V geometry:margin=0.7in \
      -V fontsize=11pt \
      -V linestretch=1.06 \
      -o "${output_dir}/resume.pdf"
  fi
}

require_binary pandoc
require_binary "$PDF_ENGINE"

if [[ ! -f "${DOCX_REFERENCE}" ]]; then
  echo "Missing DOCX reference template: ${DOCX_REFERENCE}" >&2
  exit 1
fi

if [[ ! -f "${PDF_HEADER}" ]]; then
  echo "Missing PDF header template: ${PDF_HEADER}" >&2
  exit 1
fi

if [[ -z "${MAIN_FONT}" ]] && command -v fc-list >/dev/null 2>&1; then
  for candidate in "DejaVu Sans" "Arial Unicode MS" "Liberation Sans" "Noto Sans"; do
    if grep -qi "${candidate}" < <(fc-list : family); then
      MAIN_FONT="$candidate"
      break
    fi
  done
fi

build_resume "en"
build_resume "ru"

echo "Resume files generated successfully."
