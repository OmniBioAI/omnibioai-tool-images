#!/usr/bin/env bash
#
# build_multiarch_sifs.sh
#
# Builds x86_64 (amd64) AND arm64 .sif images for the 28 selected OmniBioAI tools,
# using existing Dockerfiles that currently only have arm64 .sif builds.
#
# Strategy:
#   1. Spin up a local Docker registry (so buildx has somewhere to push multi-arch manifests).
#   2. Create a buildx builder with QEMU emulation enabled (cross-arch builds without native hosts).
#   3. For each tool: docker buildx build --platform linux/amd64,linux/arm64 --push
#   4. Pull each arch-specific image from the local registry and convert to .sif with apptainer,
#      writing <tool>_amd64.sif and <tool>_arm64.sif into sif/OmniBioAI-SIFs/
#
# Run this from: ~/Desktop/machine/omnibioai-tool-images
#
set -euo pipefail

BASE_DIR="$HOME/Desktop/machine/omnibioai-tool-images"
DOCKERFILE_DIR="$BASE_DIR/dockerfiles"
SIF_DIR="$BASE_DIR/sif/OmniBioAI-SIFs"
REGISTRY_PORT=5000
REGISTRY="localhost:${REGISTRY_PORT}"

# --- 28 tools selected ---
TOOLS=(
  genrich_extra
  sicer2_extra
  hint_extra
  footprint_extra
  centipede_extra
  wellington_extra
  jaspar_extra
  signac_extra
  pycistopic_extra
  scopen_extra
  combat_extra
  dreamlet_extra
  distinct_extra
  normalizefacs_extra
  propeller_extra
  biocmanager_extra
  juicer_extra
  dovetail_extra
  distiller_extra
  matlock_extra
  fanc_extra
  mustache_extra
  tadbit_extra
  salsa2_extra
  breakdancer_extra
  lumpy_extra
  nanovar_extra
  ichor_extra
  spectre_extra
  bbduk_v2_extra
  biobambam_extra
)

echo "==> Step 1: Start local Docker registry (if not already running)"
if ! docker ps --format '{{.Names}}' | grep -q '^local-registry$'; then
  docker run -d --restart=always -p ${REGISTRY_PORT}:5000 --name local-registry registry:2
else
  echo "local-registry already running"
fi

echo "==> Step 2: Ensure buildx builder with multi-platform (QEMU) support exists"
docker run --rm --privileged tonistiigi/binfmt --install all >/dev/null 2>&1 || true

if ! docker buildx inspect multiarch-builder >/dev/null 2>&1; then
  docker buildx create --name multiarch-builder --driver docker-container --use
else
  docker buildx use multiarch-builder
fi
docker buildx inspect --bootstrap

mkdir -p "$SIF_DIR"

FAILED=()

for tool in "${TOOLS[@]}"; do
  dockerfile="${DOCKERFILE_DIR}/Dockerfile.${tool}"
  if [[ ! -f "$dockerfile" ]]; then
    echo "!! Missing Dockerfile for ${tool}, skipping"
    FAILED+=("$tool (no dockerfile)")
    continue
  fi

  image="${REGISTRY}/omnibioai/${tool}:latest"
  echo ""
  echo "=================================================="
  echo "==> Building ${tool} for linux/amd64 + linux/arm64"
  echo "=================================================="

  if ! docker buildx build \
      --platform linux/amd64,linux/arm64 \
      -f "$dockerfile" \
      -t "$image" \
      --push \
      "$DOCKERFILE_DIR"; then
    echo "!! Docker build failed for ${tool}"
    FAILED+=("$tool (docker build failed)")
    continue
  fi

  for arch in amd64 arm64; do
    sif_path="${SIF_DIR}/${tool}_${arch}.sif"
    echo "--> Converting ${tool} (${arch}) to Apptainer .sif"

    if ! apptainer build --fakeroot \
        --arch "$arch" \
        "$sif_path" \
        "docker://${image}"; then
      echo "!! Apptainer build failed for ${tool} (${arch})"
      FAILED+=("$tool (${arch} sif build failed)")
    fi
  done
done

echo ""
echo "==================== SUMMARY ===================="
echo "Attempted: ${#TOOLS[@]} tools x 2 arches"
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "All builds succeeded."
else
  echo "Failures:"
  printf '  - %s\n' "${FAILED[@]}"
fi