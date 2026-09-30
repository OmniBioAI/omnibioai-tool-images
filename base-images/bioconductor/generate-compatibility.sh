#!/usr/bin/env bash
set -euo pipefail

matrix=${1:?workload matrix CSV required}
output=${2:?output CSV required}
mkdir -p "$(dirname "$output")"
awk -F, '
BEGIN { OFS=","; print "workload_id,classification,observed_r,observed_bioc,observed_base,canonical_line,compatibility,reason" }
NR == 1 { next }
$5 !~ /BIOCONDUCTOR/ { next }
{
  if ($5 == "R_BIOCONDUCTOR_HEAVY") {
    result="SPECIALIZED_HEAVY"
    reason="heavy package or mixed system footprint; downstream image required"
  } else if ($7 ~ /3_19/) {
    result="LEGACY_BIOC_319_EXCEPTION"
    reason="Bioconductor 3.19 is R 4.4-compatible but is not the canonical 3.20 cohort"
  } else if ($6 ~ /4\.3/) {
    result="R43_COMPATIBILITY_EXCEPTION"
    reason="observed R 4.3 requires Bioconductor 3.18; test or retain a legacy downstream image"
  } else {
    result="CAN_USE_R44_BIOC320_AFTER_PACKAGE_TEST"
    reason="candidate for canonical foundation; workload package lock and runtime test remain downstream"
  }
  print $2,$5,$6,$7,$11,"R 4.4 / Bioconductor 3.20",result,reason
}' "$matrix" > "$output"
