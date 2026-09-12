#!/usr/bin/env bash
# Build missing Docker images for linux/arm64 and convert to Singularity SIF.
# Usage (run from repo root): bash scripts/build_new_tools.sh [tool1 tool2 ...]
#   No args = build all missing tools (defined in ALL_TOOLS array).
#   With args = build only the named tools.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIF_DIR="${ROOT_DIR}/sif"
DOCKERFILE_DIR="${ROOT_DIR}/dockerfiles"
LOG_DIR="${ROOT_DIR}/build_logs"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
MAIN_LOG="${LOG_DIR}/build_${TIMESTAMP}.log"

mkdir -p "${SIF_DIR}" "${LOG_DIR}"

# ── All tools that should have SIF images ────────────────────────────────────
ALL_TOOLS=(
  # Epitranscriptomics / RNA
  ciri2 merip clipper ribotish reditools2 feelnc mirdeep2
  # Metagenomics / assembly
  megahit metabat2 checkm2 kaiju virsorter2 masurca
  # Chromatin / epigenomics
  cutrun hicpro cooltools tobias dss bsmap atac_pipeline
  # Population genetics
  whatshap faststructure sweed prsice2 shapeit5 impute5
  # Structural biology / ML
  alphafold2_multimer rosettafold proteinmpnn relion autodock_gpu
  # Long-read / nanopore
  sniffles2 modkit flames
  # Imaging / spatial
  steinbock mcmicro
  # AI / foundation models
  genomic_fm mol_gnn scbridge scdiffusion
  # Spatial transcriptomics
  rctd spatialde cellchat giotto
  # Proteomics
  xlinkx hdxms
  # SV / CNV / variant
  gridss cnvkit cadd delly
  # Single-cell
  cellrank archr sctype milo grnboost2
  # RNA-seq
  nfcore_rnaseq sleuth swish tetranscripts wgcna
  # Clinical / cancer
  intervar msisensor2 sigprofiler pvacseq msiseq
  # NGS core
  picard bowtie2
  # Annotation
  annovar polyphen2
  # Phylogenetics / alignment
  iqtree2 muscle5
  # R analysis packages
  purple hint fastqtl spotlight cibersortx progeny liger nmf_decomposition survival
  # Gene fusion
  starfusion arriba
  # Motif / QC
  memechip rseqc facets
)

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

log()   { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*" | tee -a "${MAIN_LOG}"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"    | tee -a "${MAIN_LOG}"; }
warn()  { echo -e "${YELLOW}[SKIP]${NC} $*" | tee -a "${MAIN_LOG}"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"   | tee -a "${MAIN_LOG}"; }

# ── Track results ─────────────────────────────────────────────────────────────
BUILT=(); SKIPPED=(); FAILED=()

build_tool() {
  local tool="$1"
  local sif="${SIF_DIR}/${tool}_arm64.sif"
  local dockerfile="${DOCKERFILE_DIR}/Dockerfile.${tool}"
  local tool_log="${LOG_DIR}/${tool}_${TIMESTAMP}.log"
  local image="omnibioai/${tool}:arm64"

  # Skip if SIF already exists
  if [[ -f "${sif}" ]]; then
    warn "${tool}: SIF already exists → skipping"
    SKIPPED+=("${tool}")
    return 0
  fi

  # Check Dockerfile exists
  if [[ ! -f "${dockerfile}" ]]; then
    fail "${tool}: No Dockerfile found at ${dockerfile}"
    FAILED+=("${tool} (no Dockerfile)")
    return 1
  fi

  log "Building ${tool} ..."

  # Build Docker image (native aarch64 host — no buildx needed)
  if ! docker build \
      -t "${image}" \
      -f "${dockerfile}" \
      "${ROOT_DIR}" \
      > "${tool_log}" 2>&1; then
    fail "${tool}: Docker build failed — see ${tool_log}"
    FAILED+=("${tool} (docker build)")
    return 1
  fi

  # Convert to SIF
  log "Converting ${tool} to SIF ..."
  if ! singularity pull \
      --force \
      "${sif}" \
      "docker-daemon:${image}" \
      >> "${tool_log}" 2>&1; then
    fail "${tool}: singularity build failed — see ${tool_log}"
    FAILED+=("${tool} (singularity build)")
    # Clean up dangling docker image
    docker rmi "${image}" &>/dev/null || true
    return 1
  fi

  # Remove docker image after successful SIF conversion to free disk
  docker rmi "${image}" &>/dev/null || true

  ok "${tool}: SIF written to ${sif}"
  BUILT+=("${tool}")
}

# ── Determine which tools to build ───────────────────────────────────────────
if [[ $# -gt 0 ]]; then
  TOOLS_TO_BUILD=("$@")
else
  TOOLS_TO_BUILD=("${ALL_TOOLS[@]}")
fi

log "===== OmniBioAI SIF build run ====="
log "Target: ${#TOOLS_TO_BUILD[@]} tool(s)"
log "Log dir: ${LOG_DIR}"
echo ""

for tool in "${TOOLS_TO_BUILD[@]}"; do
  build_tool "${tool}" || true
  echo ""
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
log "===== BUILD SUMMARY ====="
echo ""

if [[ ${#BUILT[@]} -gt 0 ]]; then
  echo -e "${GREEN}BUILT (${#BUILT[@]}):${NC}"
  for t in "${BUILT[@]}"; do echo "  ✓ ${t}"; done
  echo ""
fi

if [[ ${#SKIPPED[@]} -gt 0 ]]; then
  echo -e "${YELLOW}SKIPPED — already exist (${#SKIPPED[@]}):${NC}"
  for t in "${SKIPPED[@]}"; do echo "  - ${t}"; done
  echo ""
fi

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo -e "${RED}FAILED (${#FAILED[@]}):${NC}"
  for t in "${FAILED[@]}"; do echo "  ✗ ${t}"; done
  echo ""
  echo "Check per-tool logs in ${LOG_DIR}/"
fi

TOTAL_REMAINING=$(( ${#ALL_TOOLS[@]} - $(ls "${SIF_DIR}"/*_arm64.sif 2>/dev/null | wc -l) ))
echo "SIF files in ${SIF_DIR}: $(ls "${SIF_DIR}"/*_arm64.sif 2>/dev/null | wc -l)"
echo ""
echo "Full log: ${MAIN_LOG}"

# Exit non-zero if any build failed
[[ ${#FAILED[@]} -eq 0 ]]
