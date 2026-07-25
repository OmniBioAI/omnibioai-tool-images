#!/bin/bash
# build_fallback_chain.sh
#
# Fallback chain for bioconda/conda-forge packages with no linux-aarch64 build.
# Tries, in order:
#   1. pip install (many Python-based tools have aarch64 wheels even when conda doesn't)
#   2. x86_64 emulated conda build via QEMU (docker buildx --platform linux/amd64)
#   3. Give up, log to failed_final.txt for manual/source-build follow-up
#
# Usage:
#   cd /home/manish/Desktop/machine/omnibioai-tool-images
#   bash build_fallback_chain.sh
#
# Requires: docker buildx with qemu emulation support.
# One-time setup if not already configured:
#   docker run --privileged --rm tonistiigi/binfmt --install all
#   docker buildx create --use --name multiarch-builder 2>/dev/null || true

set -uo pipefail

# ---- Config ----
TOOLS=(
  pbmm2 gem3_mapper wtdbg2
  somaticsniper svtyper duphold smoove hipstr breakdancer deepsomatic
  rnaseqc fusioncatcher jaffa whippet salmontools spladder
  cellphonedb celloracle cytoflow cellcharter stereoscope souporcell stardist destiny pycistopic diffxpy bento_tools
  juicer genrich gopeaks methylpy nucleoatac rgt sicer2 gem_peakcaller
  anvio roary panaroo snippy metawrap phyloflash deblur
  casanovo protti asari crux_toolkit metfrag maldiquant
  openfold omegafold vina igfold pytraj apbs
  bitsandbytes torchdrug scispacy
  samstat
)

DOCKERFILES_DIR="dockerfiles"
LOG_DIR="build_logs/fallback_chain_$(date +%Y%m%d_%H%M%S)"
SUCCEEDED_LOG="$LOG_DIR/succeeded.txt"
FAILED_LOG="$LOG_DIR/failed_final.txt"
SUMMARY_LOG="$LOG_DIR/summary.txt"

mkdir -p "$LOG_DIR"
touch "$SUCCEEDED_LOG" "$FAILED_LOG"

# ---- Helpers ----

log() {
  echo "[$(date '+%H:%M:%S')] $*" | tee -a "$SUMMARY_LOG"
}

# Tier 1: try pip install for the same package name.
# Not all conda package names match PyPI names exactly — this is a best-effort
# pass; real per-tool corrections belong in TOOL_PIP_NAME_OVERRIDES below.
declare -A TOOL_PIP_NAME_OVERRIDES=(
  [bento_tools]="bento-tools"
  [scispacy]="scispacy"
  [torchdrug]="torchdrug"
  [bitsandbytes]="bitsandbytes"
  [diffxpy]="diffxpy"
  [protti]="protti"
)

try_pip() {
  local tool="$1"
  local pip_name="${TOOL_PIP_NAME_OVERRIDES[$tool]:-$tool}"

  cat > "$DOCKERFILES_DIR/Dockerfile.${tool}_pipfallback" <<EOF
FROM python:3.11-slim
RUN pip install --no-cache-dir ${pip_name}
EOF

  docker build --no-cache \
    -f "$DOCKERFILES_DIR/Dockerfile.${tool}_pipfallback" \
    -t "${tool}:latest" . \
    > "$LOG_DIR/${tool}_pip.log" 2>&1

  local status=$?
  rm -f "$DOCKERFILES_DIR/Dockerfile.${tool}_pipfallback"
  return $status
}

# Tier 2: emulated x86_64 conda build. Slower at runtime (QEMU translation
# layer) but works when no native aarch64 build exists on bioconda/conda-forge.
try_qemu_conda() {
  local tool="$1"
  local native_dockerfile="$DOCKERFILES_DIR/Dockerfile.${tool}"

  if [ ! -f "$native_dockerfile" ]; then
    log "  SKIP qemu tier for $tool — no existing Dockerfile.${tool} to reuse"
    return 1
  fi

  docker buildx build --no-cache \
    --platform linux/amd64 \
    -f "$native_dockerfile" \
    -t "${tool}:latest" \
    --load \
    . \
    > "$LOG_DIR/${tool}_qemu.log" 2>&1

  return $?
}

# ---- Main loop ----

log "Starting fallback chain for ${#TOOLS[@]} tools"
log "Logs: $LOG_DIR"
echo

for tool in "${TOOLS[@]}"; do
  log "=== $tool ==="

  if try_pip "$tool"; then
    log "  ✅ succeeded via pip"
    echo "$tool (pip)" >> "$SUCCEEDED_LOG"
    continue
  fi
  log "  pip failed, trying x86_64 emulation..."

  if try_qemu_conda "$tool"; then
    log "  ✅ succeeded via qemu/x86_64 emulation (slower at runtime — flag for native rebuild later)"
    echo "$tool (qemu-x86_64)" >> "$SUCCEEDED_LOG"
    continue
  fi
  log "  ❌ all fallback tiers failed"
  echo "$tool" >> "$FAILED_LOG"

  echo
done

# ---- Summary ----
succeeded_count=$(wc -l < "$SUCCEEDED_LOG")
failed_count=$(wc -l < "$FAILED_LOG")

log ""
log "==================================================="
log "Fallback chain complete: $succeeded_count / ${#TOOLS[@]} succeeded"
log "Still failing: $failed_count (see $FAILED_LOG)"
log "==================================================="

if [ "$failed_count" -gt 0 ]; then
  log ""
  log "Tools requiring manual source-build or substitution:"
  cat "$FAILED_LOG" | tee -a "$SUMMARY_LOG"
fi
