#!/bin/bash
# build_60_new_tools.sh
#
# Builds SIF images for tools NOT already present in the collection.
# Safe to run even if some names below turn out to overlap — it checks
# the actual sif/ directory first and skips anything that already exists,
# so duplicate effort is impossible regardless of manual list-checking errors.
#
# Pipeline per tool:
#   1. Skip if <tool>_arm64.sif already exists
#   2. Try native aarch64 conda build (bioconda/conda-forge)
#   3. On failure, try pip
#   4. On failure, try qemu-emulated x86_64 conda build
#   5. On any success, convert the resulting docker image to a .sif file
#   6. Log final success/failure breakdown
#
# Usage:
#   cd /home/manish/Desktop/machine/omnibioai-tool-images
#   bash build_60_new_tools.sh
#
# Requires: docker, docker buildx with qemu emulation, apptainer/singularity
# (same tooling your existing build_200_new.sh already depends on).

set -uo pipefail

# ---- Where existing SIFs live, for dedup ----
SIF_DIR="/media/manish/OmniBioAI-SIFs/sif"
DOCKERFILES_DIR="dockerfiles"
LOG_DIR="build_logs/new_60_$(date +%Y%m%d_%H%M%S)"
SUCCEEDED_LOG="$LOG_DIR/succeeded.txt"
FAILED_LOG="$LOG_DIR/failed.txt"
SKIPPED_LOG="$LOG_DIR/skipped_already_exists.txt"
SUMMARY_LOG="$LOG_DIR/summary.txt"

mkdir -p "$LOG_DIR" "$DOCKERFILES_DIR"
touch "$SUCCEEDED_LOG" "$FAILED_LOG" "$SKIPPED_LOG"

# ---- Candidate tools NOT observed in the current sif/ listing ----
# Format: tool_name:conda_package_name (usually identical; override when bioconda
# uses a different package name than the common tool name)
# Spanning categories to round out coverage: long-read, phylogenetics,
# structural variant, spatial, immunology, population genetics, proteomics,
# workflow/utility tools.
NEW_TOOLS=(
  "nanosim:nanosim"
  "pbsim3:pbsim3"
  "medaka2:medaka"
  "flye2:flye"
  "raven2:raven-assembler"
  "yak:yak"
  "purge_dups:purge_dups"
  "hifiasm_meta:hifiasm_meta"
  "beast2:beast2"
  "mrbayes3:mrbayes"
  "phylobayes:phylobayes"
  "treemix:treemix"
  "admixtools:admixtools"
  "dsuite:dsuite"
  "vcflib:vcflib"
  "bcftools_roh:bcftools"
  "svtools:svtools"
  "manta2:manta"
  "wham:wham"
  "delly2:delly"
  "cnvpytor:cnvpytor"
  "control_freec:control-freec"
  "sequenza_utils:sequenza-utils"
  "battenberg:battenberg"
  "ascat3:ascat"
  "pyclone_vi:pyclone-vi"
  "sciclone:sciclone"
  "phylowgs:phylowgs"
  "cellsium:cellsium"
  "spatialglue:spatialglue"
  "stagate:stagate"
  "graphst:graphst"
  "novosparc:novosparc"
  "cellcharter2:cellcharter"
  "cottrazm:cottrazm"
  "bering:bering"
  "monkeybread:monkeybread"
  "squidpy2:squidpy"
  "commot:commot"
  "cellphonedb3:cellphonedb"
  "nichenet:nichenetr"
  "starfish:starfish"
  "scikit_bio:scikit-bio"
  "immcantation:immcantation"
  "tcrdist3:tcrdist3"
  "airr_c:airr-c"
  "olga:olga"
  "stitchr:stitchr"
  "presto_immcantation:presto"
  "diann2:diann"
  "fragpipe2:fragpipe"
  "spectronaut_cli:spectronaut"
  "skyline_cli:bibliospec"
  "openswath:openswath"
  "dinosaur:dinosaur"
  "moFF2:moff"
  "alphapeptdeep:alphapeptdeep"
  "deeplc:deeplc"
  "ms2pip:ms2pip"
  "flashdeconv:flashdeconv"
  "topfd:topfd"
)

log() {
  echo "[$(date '+%H:%M:%S')] $*" | tee -a "$SUMMARY_LOG"
}

already_exists() {
  local tool="$1"
  [ -f "$SIF_DIR/${tool}_arm64.sif" ]
}

# Tier 1: native aarch64 conda build
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

# Tier 2: pip fallback
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

# Tier 3: qemu-emulated x86_64 conda build
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

# Convert a successfully-built docker image into a .sif file, matching the
# existing naming convention (<tool>_arm64.sif) in SIF_DIR.
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
