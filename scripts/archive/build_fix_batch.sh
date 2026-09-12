#!/usr/bin/env bash
# build_fix_batch.sh — Build the 62 remaining failed tools after Dockerfile fixes
set -euo pipefail

SIF_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/sif
DF_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/dockerfiles
LOG_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/build_logs
mkdir -p "$LOG_DIR"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

BUILT=0; FAILED=0; SKIPPED=0

build_tool() {
  local name=$1
  local sif="${SIF_DIR}/${name}_arm64.sif"
  local log="${LOG_DIR}/${name}.log"
  local df="${DF_DIR}/Dockerfile.${name}"
  local tag="${name,,}"

  if [ -f "$sif" ]; then
    echo -e "${YELLOW}SKIP${NC}  ${name} (SIF exists)"
    ((SKIPPED++)) || true; return 0
  fi
  if [ ! -f "$df" ]; then
    echo -e "${RED}MISS${NC}  ${name} (no Dockerfile)"
    ((FAILED++)) || true; return 1
  fi

  echo -e "${YELLOW}BUILD${NC} ${name}..."
  > "$log"
  if docker build --platform linux/arm64 -t "${tag}:latest" -f "$df" . >> "$log" 2>&1; then
    if singularity build "$sif" "docker-daemon://${tag}:latest" >> "$log" 2>&1; then
      echo -e "${GREEN}OK${NC}    ${name}"
      ((BUILT++)) || true
    else
      echo -e "${RED}FAIL${NC}  ${name} (singularity failed — see $log)"
      ((FAILED++)) || true
    fi
  else
    echo -e "${RED}FAIL${NC}  ${name} (docker failed — see $log)"
    ((FAILED++)) || true
  fi
}

echo "=== OmniBioAI fix-batch rebuild — $(date) ==="
echo

# Group 1: Stubs (fast — use cached python:3.11-slim layers)
echo "--- Stubs (x86-only / missing ARM64 packages) ---"
for t in \
  autodock_gpu baysor bsmap ciri2 cobalt diann flare_ancestry fpocket impute5 \
  intervar masurca metamorpheus mixcr msfragger fragpipe msisensor2 opera_ms_assembly \
  prsice2 purple schmutzi starfusion virsorter2 gnina alphafold2_multimer rosettafold \
  picrust2 braker3_annotation pggb_pangenome amber qiime2 pvacseq clipper hint; do
  build_tool "$t"
done

# Group 2: Python=3.11 conda fixes
echo "--- Conda python=3.11 fixes ---"
for t in atac_pipeline cutrun fastqscreen lumpy_sv mirdeep2 trinity_assembly_adv cactus_alignment openms; do
  build_tool "$t"
done

# Group 3: Misc fixes
echo "--- Misc fixes ---"
for t in beagle5_impute ldsc_heritability checkm2 snapatac2 tobias; do
  build_tool "$t"
done

# Group 4: Pip package fixes
echo "--- Pip/package fixes ---"
for t in talon_transcriptome trycycler_consensus arcashla mageckvispr_screen flames \
          dastool unicycler_hybrid mcmicro alphamissense; do
  build_tool "$t"
done

# Group 5: Pip-fix tools still in flight (if not yet built)
echo "--- Network-retry tools ---"
for t in hicpro hifiasm sweed canu_assembly hdxms vamb_binning; do
  build_tool "$t"
done

# Group 6: ML tools (heavy, last)
echo "--- ML tools (scvi-stack + others) ---"
for t in scvi cell2location cell2location_deconv totalvi scgpt chai1 esm3; do
  build_tool "$t"
done

echo
echo "=== Done: BUILT=$BUILT  FAILED=$FAILED  SKIPPED=$SKIPPED ==="
