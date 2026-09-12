#!/usr/bin/env bash
# build_failed_tools.sh — Rebuild only the 26 tools that failed in the last run
set -euo pipefail

SIF_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/sif
DF_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/dockerfiles
LOG_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/build_logs
mkdir -p "$LOG_DIR"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

TOTAL=26
BUILT=0; FAILED=0; SKIPPED=0

build_tool() {
  local name=$1 idx=$2
  local sif="${SIF_DIR}/${name}_arm64.sif"
  local log="${LOG_DIR}/${name}.log"
  local df="${DF_DIR}/Dockerfile.${name}"

  if [ -f "$sif" ]; then
    echo -e "${YELLOW}SKIP${NC}  [$idx/$TOTAL] ${name} (SIF exists)"
    ((SKIPPED++)) || true; return 0
  fi
  if [ ! -f "$df" ]; then
    echo -e "${RED}MISS${NC}  [$idx/$TOTAL] ${name} (no Dockerfile)"
    ((FAILED++)) || true; return 1
  fi

  local tag="${name,,}"
  echo -e "${YELLOW}BUILD${NC} [$idx/$TOTAL] ${name}..."
  # Truncate old log then append fresh output
  > "$log"
  if docker build --platform linux/arm64 --no-cache -t "${tag}:latest" -f "$df" . >> "$log" 2>&1; then
    if singularity build "$sif" "docker-daemon://${tag}:latest" >> "$log" 2>&1; then
      echo -e "${GREEN}OK${NC}    [$idx/$TOTAL] ${name}"
      ((BUILT++)) || true
    else
      echo -e "${RED}FAIL${NC}  [$idx/$TOTAL] ${name} (singularity build failed — see $log)"
      ((FAILED++)) || true
    fi
  else
    echo -e "${RED}FAIL${NC}  [$idx/$TOTAL] ${name} (docker build failed — see $log)"
    ((FAILED++)) || true
  fi
}

echo "=== OmniBioAI failed-tools rebuild — $(date) ==="
echo "Rebuilding 26 previously failed SIFs"
echo

build_tool bbtools 1
build_tool bismark_align_adv 2
build_tool deconstructsigs 3
build_tool deeptools_bamcompare 4
build_tool deeptools_bamcoverage_adv 5
build_tool deseq2_de 6
build_tool dexseq_exon 7
build_tool feelnc 8
build_tool freebayes_call 9
build_tool gtdbtk 10
build_tool htseq_count 11
build_tool humann3_pathway 12
build_tool kallisto_quant_adv 13
build_tool ldsc_heritability 14
build_tool mageck 15
build_tool mageck_count_adv 16
build_tool mcscan_synteny 17
build_tool moff 18
build_tool orthofinder_orthologs 19
build_tool pangolin 20
build_tool picrust2 21
build_tool pmdtools 22
build_tool primedesign_pegrna 23
build_tool reditools2 24
build_tool rsem_quant 25
build_tool semibin_binning 26

echo
echo "=== Done: BUILT=$BUILT  FAILED=$FAILED  SKIPPED=$SKIPPED ==="
