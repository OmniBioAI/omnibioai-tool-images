#!/bin/bash
# Build all ARM64 Docker images and convert to Singularity SIF
# Usage (run from repo root): bash scripts/build_all.sh [tool_name]
# Example: bash scripts/build_all.sh fastqc
# Example: bash scripts/build_all.sh  (builds all)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOCKERFILE_DIR="$ROOT_DIR/dockerfiles"
SIF_DIR="$ROOT_DIR/sif"
LOG_DIR="$ROOT_DIR/build_logs"

mkdir -p "$SIF_DIR" "$LOG_DIR"

build_tool() {
    local TOOL=$1
    local DOCKERFILE="$DOCKERFILE_DIR/Dockerfile.$TOOL"
    local IMAGE="omnibioai/$TOOL:arm64"
    local SIF="$SIF_DIR/${TOOL}_arm64.sif"
    local LOG="$LOG_DIR/$TOOL.log"

    if [ ! -f "$DOCKERFILE" ]; then
        echo "❌ $TOOL — Dockerfile not found: $DOCKERFILE"
        return 1
    fi

    echo "🔨 Building $TOOL..."
    docker build \
        -t "$IMAGE" \
        -f "$DOCKERFILE" \
        "$DOCKERFILE_DIR" \
        > "$LOG" 2>&1

    if [ $? -ne 0 ]; then
        echo "❌ $TOOL — Docker build failed. See $LOG"
        return 1
    fi
    echo "✅ $TOOL — Docker image built"

    echo "📦 Converting $TOOL to SIF..."
    singularity pull \
        --force \
        "$SIF" \
        "docker-daemon:$IMAGE" \
        >> "$LOG" 2>&1

    if [ $? -ne 0 ]; then
        echo "❌ $TOOL — Singularity pull failed. See $LOG"
        return 1
    fi
    echo "✅ $TOOL — SIF created: $SIF"
    echo ""
}

# Build specific tool or all
if [ -n "${1:-}" ]; then
    build_tool "$1"
else
    for dockerfile in "$DOCKERFILE_DIR"/Dockerfile.*; do
        TOOL=$(basename "$dockerfile" | sed 's/Dockerfile\.//')
        build_tool "$TOOL"
    done
fi

echo "🎉 Done! SIF images in $SIF_DIR"
ls -lh "$SIF_DIR"
