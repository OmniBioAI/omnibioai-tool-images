#!/bin/bash
# build_20_more_tools.sh
#
# Same pattern as build_60_new_tools.sh: dedup against existing SIFs first,
# then native aarch64 conda -> pip -> qemu x86_64 emulation -> SIF conversion.
#
# Usage:
#   cd /home/manish/Desktop/machine/omnibioai-tool-images
#   bash build_20_more_tools.sh

set -uo pipefail

SIF_DIR="/media/manish/OmniBioAI-SIFs/sif"
DOCKERFILES_DIR="dockerfiles"
LOG_DIR="build_logs/new_20_$(date +%Y%m%d_%H%M%S)"
SUCCEEDED_LOG="$LOG_DIR/succeeded.txt"
FAILED_LOG="$LOG_DIR/failed.txt"
SKIPPED_LOG="$LOG_DIR/skipped_already_exists.txt"
SUMMARY_LOG="$LOG_DIR/summary.txt"

mkdir -p "$LOG_DIR" "$DOCKERFILES_DIR"
touch "$SUCCEEDED_LOG" "$FAILED_LOG" "$SKIPPED_LOG"

# ---- 20 new candidates, avoiding anything attempted in the previous 61-tool
# run (whether it succeeded or failed there). Spanning: metagenomics,
# structural biology / cryo-EM, RNA modification, long-read utilities,
# pangenomics, and general workflow tooling — categories not yet covered.
NEW_TOOLS=(
  "kaiju2:kaiju"
  "bracken2:bracken"
  "krakenuniq:krakenuniq"
  "sourmash2:sourmash"
  "gtdbtk2:gtdbtk"
  "checkm2b:checkm2"
  "cryosparc_tools:cryosparc-tools"
  "relion5:relion"
  "cistem:cistem"
  "chimerax_cli:chimerax"
  "eman2:eman2"
  "nanopolish:nanopolish"
  "tombo:ont-tombo"
  "xpore:xpore"
  "m6anet:m6anet"
  "pangenie:pangenie"
  "minigraph2:minigraph"
  "vg_giraffe:vg"
  "nextflow2:nextflow"
  "snakemake_wrapper:snakemake-wrapper-utils"
)

log() {
  echo "[$(date '+%H:%M:%S')] $*" | tee -a "$SUMMARY_LOG"
}

already_exists() {
  local tool="$1"
  [ -f "$SIF_DIR/${tool}_arm64.sif" ]
}

try_native_conda() {
  local tool="$1"
  local pkg="$2"

  cat > "$DOCKERFILES_DIR/Dockerfile.${tool}" <<EOF
FROM mambaorg/micromamba:latest
RUN micromamba install -y -n base -c bioconda -c conda-forge ${pkg} \\
    && micromamba clean --all --yes
EOF

  docker build --no-cache \
    -f "$DOCKERFILES_DIR/Dockerfile.${tool}" \
    -t "${tool}:latest" . \
    > "$LOG_DIR/${tool}_native.log" 2>&1
  return $?
}

try_pip() {
  local tool="$1"
  local pkg="$2"

  cat > "$DOCKERFILES_DIR/Dockerfile.${tool}_pipfallback" <<EOF
FROM python:3.11-slim
RUN pip install --no-cache-dir ${pkg}
EOF

  docker build --no-cache \
    -f "$DOCKERFILES_DIR/Dockerfile.${tool}_pipfallback" \
    -t "${tool}:latest" . \
    > "$LOG_DIR/${tool}_pip.log" 2>&1
  local status=$?
  rm -f "$DOCKERFILES_DIR/Dockerfile.${tool}_pipfallback"
  return $status
}

try_qemu_conda() {
  local tool="$1"

  docker buildx build --no-cache \
    --platform linux/amd64 \
    -f "$DOCKERFILES_DIR/Dockerfile.${tool}" \
    -t "${tool}:latest" \
    --load \
    . \
    > "$LOG_DIR/${tool}_qemu.log" 2>&1
  return $?
}

convert_to_sif() {
  local tool="$1"
  singularity build --force \
    "$SIF_DIR/${tool}_arm64.sif" \
    "docker-daemon://${tool}:latest" \
    > "$LOG_DIR/${tool}_sifconvert.log" 2>&1
  return $?
}

# ---- Main loop ----

log "Checking ${#NEW_TOOLS[@]} candidate tools against existing collection at $SIF_DIR"
echo

for entry in "${NEW_TOOLS[@]}"; do
  tool="${entry%%:*}"
  pkg="${entry##*:}"

  if already_exists "$tool"; then
    log "SKIP $tool — ${tool}_arm64.sif already exists"
    echo "$tool" >> "$SKIPPED_LOG"
    continue
  fi

  log "=== $tool (package: $pkg) ==="

  built=0

  if try_native_conda "$tool" "$pkg"; then
    log "  ✅ native aarch64 conda build succeeded"
    built=1
  else
    log "  native build failed, trying pip..."
    if try_pip "$tool" "$pkg"; then
      log "  ✅ pip fallback succeeded"
      built=1
    else
      log "  pip failed, trying x86_64 emulation..."
      if try_qemu_conda "$tool"; then
        log "  ✅ qemu/x86_64 emulation succeeded (slower at runtime — flag for native rebuild later)"
        built=1
      fi
    fi
  fi

  if [ "$built" -eq 1 ]; then
    if convert_to_sif "$tool"; then
      log "  ✅ converted to ${tool}_arm64.sif"
      echo "$tool" >> "$SUCCEEDED_LOG"
    else
      log "  ❌ docker image built but SIF conversion failed — see ${tool}_sifconvert.log"
      echo "$tool (sif-convert-failed)" >> "$FAILED_LOG"
    fi
  else
    log "  ❌ all build tiers failed"
    echo "$tool" >> "$FAILED_LOG"
  fi

  echo
done

# ---- Summary ----
succeeded_count=$(wc -l < "$SUCCEEDED_LOG")
failed_count=$(wc -l < "$FAILED_LOG")
skipped_count=$(wc -l < "$SKIPPED_LOG")

log ""
log "==================================================="
log "New builds succeeded: $succeeded_count"
log "Already existed (skipped): $skipped_count"
log "Failed: $failed_count"
log "Total collection size now: $(ls "$SIF_DIR"/*.sif 2>/dev/null | wc -l) SIF files"
log "==================================================="

if [ "$failed_count" -gt 0 ]; then
  log ""
  log "Failed tools (see individual logs in $LOG_DIR):"
  cat "$FAILED_LOG" | tee -a "$SUMMARY_LOG"
fi
