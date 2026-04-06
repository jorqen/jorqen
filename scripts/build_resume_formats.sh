#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PDF_ENGINE="${PDF_ENGINE:-xelatex}"
MAIN_FONT="${MAIN_FONT:-}"

require_binary() {
  local bin_name="$1"
  if ! command -v "$bin_name" >/dev/null 2>&1; then
    echo "Required binary '$bin_name' was not found in PATH." >&2
    exit 1
  fi
}

build_resume() {
  local lang="$1"
  local title="$2"
  local source_file="${ROOT_DIR}/resume/${lang}/resume.md"
  local output_dir="${ROOT_DIR}/resume/${lang}"

  if [[ ! -f "$source_file" ]]; then
    echo "Missing source file: $source_file" >&2
    exit 1
  fi

  pandoc "$source_file" --from=gfm --to=plain -o "${output_dir}/resume.txt"
  pandoc "$source_file" --from=gfm -o "${output_dir}/resume.docx"
  if [[ -n "${MAIN_FONT}" ]]; then
    pandoc "$source_file" \
      --from=gfm \
      --pdf-engine="$PDF_ENGINE" \
      -V "mainfont=${MAIN_FONT}" \
      -V geometry:margin=1in \
      -V fontsize=11pt \
      -M "title=${title}" \
      -o "${output_dir}/resume.pdf"
  else
    pandoc "$source_file" \
      --from=gfm \
      --pdf-engine="$PDF_ENGINE" \
      -V geometry:margin=1in \
      -V fontsize=11pt \
      -M "title=${title}" \
      -o "${output_dir}/resume.pdf"
  fi
}

require_binary pandoc
require_binary "$PDF_ENGINE"

if [[ -z "${MAIN_FONT}" ]] && command -v fc-list >/dev/null 2>&1; then
  for candidate in "DejaVu Sans" "Arial Unicode MS" "Liberation Sans" "Noto Sans"; do
    if grep -qi "${candidate}" < <(fc-list : family); then
      MAIN_FONT="$candidate"
      break
    fi
  done
fi

build_resume "en" "Matvey Sizov Resume (EN)"
build_resume "ru" "Матвей Сизов Резюме (RU)"

echo "Resume files generated successfully."
