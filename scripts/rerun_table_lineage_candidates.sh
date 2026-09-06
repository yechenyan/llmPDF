#!/usr/bin/env bash

set -euo pipefail

if [[ "${1:-}" == "--worker" ]]; then
  total="$2"
  project_root="$3"
  pdf_root="$4"
  position="$5"
  relative_pdf="$6"
  pdf="$pdf_root/$relative_pdf"
  output_dir="$(dirname "$pdf")"

  printf '[%d/%d] starting %s\n' "$position" "$total" "$relative_pdf"
  rm -rf -- "$output_dir/assets" "$output_dir/work"
  rm -f -- "$output_dir/output.md"
  cd "$project_root"
  if uv run llmpdf run-all \
    --pdf "$pdf" \
    --output-dir "$output_dir" \
    --force; then
    printf '[%d/%d] completed %s\n' "$position" "$total" "$relative_pdf"
  else
    status="$?"
    printf '[%d/%d] failed %s\n' "$position" "$total" "$relative_pdf" >&2
    exit "$status"
  fi
  exit 0
fi

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_path="$project_root/scripts/$(basename "${BASH_SOURCE[0]}")"
pdf_root="${1:-/Users/maxiao/Documents/code2/NAP-markdown/nap-markdwon}"
jobs=3

pdfs=(
  # User-selected PDFs.
  "avacon_netz/netzausbauplan_2024_avacon_netz_gmbh_pdf.pdf"
  "bielefelder_netz/2024_04_30_netzausbauplan_14d_enwg_2024_bielefelder_netz_gmbh_version_1_0_pdf.pdf"
  "e_netz_suedhessen/netzausbauplan_2024_v2_pdf.pdf"
  "mvv_netze/240422_2024_bericht_14d_enwg_mvv_netze_pdf.pdf"
  "naturenergie_netze/netzausbauplan_naturenergienetze_gmbh_2024_pdf.pdf"

  # High-confidence scan results.
  "energienetze_offenbach_gmbh_eno/2024_bericht_14d_enwg_eno_aktualisiert_august_2024_pdf.pdf"
  "fairnetz/2024_netzausbauplan_nach_14d_enwg_der_fairnetz_gmbh_pdf.pdf"
  "inetz/netzausbauplan_inetz_2024_veroeffentlichung_01_07_2025_ergaenzung_pdf.pdf"
  "netze_odr/netzausbauplan_netze_odr_2024_pdf.pdf"
  "stadtwerke_ulm_neu_ulm_netze/netzausbauplan_2024_update.pdf"
  "stuttgart_netze/netzausbauplan_2024_stuttgart_netze_final_1_0_pdf.pdf"
  "travenetz/netzausbauplan_travenetz_gmbh_2024_v3_pdf.pdf"
  "wemag/wng_netzausbauplan_stand_2024_241002_pdf.pdf"

)

total="${#pdfs[@]}"

for index in "${!pdfs[@]}"; do
  relative_pdf="${pdfs[$index]}"
  pdf="$pdf_root/$relative_pdf"
  if [[ ! -f "$pdf" ]]; then
    printf 'PDF not found: %s\n' "$pdf" >&2
    exit 1
  fi
done

printf 'Starting %d PDFs with %d concurrent jobs.\n' "$total" "$jobs"
if {
  for index in "${!pdfs[@]}"; do
    printf '%s\0%s\0' "$((index + 1))" "${pdfs[$index]}"
  done
} | xargs -0 -n 2 -P "$jobs" \
  "$script_path" --worker "$total" "$project_root" "$pdf_root"; then
  printf '\nFinished: %d total, 0 failed.\n' "$total"
else
  status="$?"
  printf '\nFinished with one or more failed PDFs.\n' >&2
  exit "$status"
fi
