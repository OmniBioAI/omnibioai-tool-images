#!/usr/bin/env bash
set -uo pipefail

NEW_ORG="omnibioai"
PKG_PREFIX="omnibioai-sif"
TAG="arm64"
GH_USER="${GH_USER:?Set GH_USER env var}"
GH_TOKEN="${GH_TOKEN:?Set GH_TOKEN env var}"
SIF_DIR="${1:-.}"
LOG_FILE="./sif_push.log"
SKIPPED_FILE="./sif_skipped.txt"

touch "$LOG_FILE"
> "$SKIPPED_FILE"

if ! command -v oras > /dev/null 2>&1; then
  echo "oras not found. Installing..."
  curl -LO https://github.com/oras-project/oras/releases/download/v1.1.0/oras_1.1.0_linux_arm64.tar.gz
  tar -xzf oras_1.1.0_linux_arm64.tar.gz
  sudo mv oras /usr/local/bin/
fi

echo "$GH_TOKEN" | oras login ghcr.io -u "$GH_USER" --password-stdin

already_done() { grep -Fxq "$1" "$LOG_FILE" 2>/dev/null; }
mark_done() { echo "$1" >> "$LOG_FILE"; }
remote_tag_exists() { oras manifest fetch "$1" > /dev/null 2>&1; }

shopt -s nullglob
count_total=0; count_pushed=0; count_skipped=0; count_failed=0; count_no_match=0

for f in "$SIF_DIR"/*.sif; do
  count_total=$((count_total+1))
  base=$(basename "$f")
  case "$base" in
    *_arm64.sif) name="${base%_arm64.sif}"; name=$(echo "$name" | tr '[:upper:]' '[:lower:]') ;;
    *) echo "  [no-match] $base"; echo "$base" >> "$SKIPPED_FILE"; count_no_match=$((count_no_match+1)); continue ;;
  esac

  dst="ghcr.io/${NEW_ORG}/${PKG_PREFIX}/${name}:${TAG}"
  key="${name}:${TAG}"

  if already_done "$key"; then
    echo "  [skip] $key already pushed"
    count_skipped=$((count_skipped+1))
    continue
  fi

  if remote_tag_exists "$dst"; then
    echo "  [skip] $dst exists on GHCR"
    mark_done "$key"
    count_skipped=$((count_skipped+1))
    continue
  fi

  echo "=== Pushing $base -> $dst ==="
  if oras push --disable-path-validation "$dst" "$f"; then
    mark_done "$key"
    count_pushed=$((count_pushed+1))
    echo "✅ $name pushed!"
  else
    echo "❌ push failed for $base"
    count_failed=$((count_failed+1))
  fi
done

echo ""
echo "==== Summary ===="
echo "Total:   $count_total"
echo "Pushed:  $count_pushed"
echo "Skipped: $count_skipped"
echo "Failed:  $count_failed"
