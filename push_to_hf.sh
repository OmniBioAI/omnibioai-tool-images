#!/bin/bash
# Upload all ARM64 SIF images to Hugging Face
# Skips already uploaded files
REPO="omnibioai/omnibioai-sif-images"
SIF_DIR="sif"
LOG="build_logs/hf_push_$(date +%Y%m%d_%H%M%S).log"
PUSHED=0; SKIPPED=0; FAILED=0
mkdir -p build_logs

echo "=== HF Push $(date) ===" | tee "$LOG"
echo "Repo: $REPO" | tee -a "$LOG"
echo "SIFs to check: $(ls $SIF_DIR/*_arm64.sif | wc -l)" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# Get list of already uploaded files from HF
echo "Fetching already uploaded files from HF..." | tee -a "$LOG"
huggingface-cli repo ls-files \
  --repo-type dataset $REPO 2>/dev/null | \
  grep "_arm64.sif" | \
  awk '{print $NF}' > /tmp/hf_existing.txt

echo "Already on HF: $(wc -l < /tmp/hf_existing.txt) files" | tee -a "$LOG"
echo "" | tee -a "$LOG"

for sif in "$SIF_DIR"/*_arm64.sif; do
    filename=$(basename "$sif")
    
    # Check if already uploaded
    if grep -q "^${filename}$\|/${filename}$" /tmp/hf_existing.txt 2>/dev/null; then
        echo "⏭️  SKIP: $filename (already on HF)" | tee -a "$LOG"
        ((SKIPPED++))
        continue
    fi
    
    echo -n "⬆️  Uploading $filename ... " | tee -a "$LOG"
    if huggingface-cli upload "$REPO" "$sif" \
      --repo-type dataset >> "$LOG" 2>&1; then
        echo "✅ OK" | tee -a "$LOG"
        ((PUSHED++))
    else
        echo "❌ FAILED" | tee -a "$LOG"
        ((FAILED++))
    fi
done

echo "" | tee -a "$LOG"
echo "=======================================" | tee -a "$LOG"
echo "✅ Pushed:  $PUSHED" | tee -a "$LOG"
echo "⏭️  Skipped: $SKIPPED (already on HF)" | tee -a "$LOG"
echo "❌ Failed:  $FAILED" | tee -a "$LOG"
echo "Total on HF: $((PUSHED + SKIPPED))" | tee -a "$LOG"
echo "=======================================" | tee -a "$LOG"
