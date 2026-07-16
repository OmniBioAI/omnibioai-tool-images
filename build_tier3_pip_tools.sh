#!/usr/bin/env bash
# build_tier3_pip_tools.sh — Build ARM64 SIFs for tools installable via plain PyPI packages
# (no bioconda/quay.io/Galaxy-depot ARM64 image exists for these; built from Dockerfiles instead)
set -uo pipefail

SIF_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/sif
DF_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/dockerfiles
LOG_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/build_logs
mkdir -p "$LOG_DIR"
cd /home/manish/Desktop/machine/omnibioai-tool-images

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

TOOLS=(interpretML lightning propka pdbtools esmfold metacells dandelion deepchem chemprop pyg dgllife mummichog cardamom pyslingshot garnett ramclust scpred rehh lassosum ldpred2 wnn_multiome dssp)
TOTAL=${#TOOLS[@]}
echo "Waiting for captum SIF conversion to finish before starting..."
while pgrep -f "singularity build sif/captum_arm64.sif" > /dev/null; do sleep 5; done
BUILT=0; FAILED=0; SKIPPED=0
idx=0

for name in "${TOOLS[@]}"; do
  idx=$((idx+1))
  sif="${SIF_DIR}/${name}_arm64.sif"
  log="${LOG_DIR}/${name}.log"
  df="${DF_DIR}/Dockerfile.${name}"

  if [ -f "$sif" ]; then
    echo -e "${YELLOW}SKIP${NC}  [$idx/$TOTAL] ${name} (SIF exists)"
    SKIPPED=$((SKIPPED+1)); continue
  fi
  if [ ! -f "$df" ]; then
    echo -e "${RED}MISS${NC}  [$idx/$TOTAL] ${name} (no Dockerfile)"
    FAILED=$((FAILED+1)); continue
  fi

  tag="${name,,}"
  echo -e "${YELLOW}BUILD${NC} [$idx/$TOTAL] ${name}..."
  if docker build --platform linux/arm64 -t "${tag}:latest" -f "$df" . > "$log" 2>&1; then
    if singularity build "$sif" "docker-daemon://${tag}:latest" >> "$log" 2>&1; then
      echo -e "${GREEN}OK${NC}    [$idx/$TOTAL] ${name} ($(du -sh "$sif" | cut -f1))"
      BUILT=$((BUILT+1))
    else
      echo -e "${RED}FAIL${NC}  [$idx/$TOTAL] ${name} (singularity build failed — see $log)"
      FAILED=$((FAILED+1))
    fi
  else
    echo -e "${RED}FAIL${NC}  [$idx/$TOTAL] ${name} (docker build failed — see $log)"
    FAILED=$((FAILED+1))
  fi
done

echo ""
echo "========================================"
echo "TIER3 PIP-TOOLS BUILD COMPLETE: $(date)"
echo "✅ Built:   $BUILT"
echo "❌ Failed:  $FAILED"
echo "⏭️  Skipped: $SKIPPED"
echo "Total SIF images now: $(ls $SIF_DIR/*.sif | wc -l)"
echo "========================================"
