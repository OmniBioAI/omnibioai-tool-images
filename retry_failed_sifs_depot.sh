#!/bin/bash
# Retry build for previously-failed SIFs using resolved Galaxy depot URLs
# (real tags verified against https://depot.galaxyproject.org/singularity/ index,
#  since quay.io/biocontainers ARM64 images were missing for these tools)
SIF_DIR=~/Desktop/machine/omnibioai-tool-images/sif
LOG=~/Desktop/machine/omnibioai-tool-images/build_logs/retry_depot_$(date +%Y%m%d_%H%M).log
mkdir -p ~/Desktop/machine/omnibioai-tool-images/build_logs
cd "$SIF_DIR"

SUCCESS=0
FAILED=0
SKIPPED=0
START_TIME=$(date)

build_sif() {
  local name=$1
  local url=$2
  local outfile="${name}_arm64.sif"

  if [ -f "$outfile" ]; then
    echo "⏭️  SKIP: $outfile already exists"
    SKIPPED=$((SKIPPED + 1))
    return
  fi

  echo "🔨 Building: $outfile from $url"
  local tmpfile="${outfile}.part"
  if curl -sL --fail --max-time 1800 -o "$tmpfile" "$url" >> "$LOG" 2>&1 \
     && file "$tmpfile" | grep -qi "singularity\|Squashfs\|gzip compressed"; then
    mv "$tmpfile" "$outfile"
    echo "✅ SUCCESS: $outfile ($(du -sh "$outfile" | cut -f1))"
    SUCCESS=$((SUCCESS + 1))
  else
    echo "   curl failed/invalid, retrying via singularity pull..." >> "$LOG"
    rm -f "$tmpfile"
    if singularity pull --name "$outfile" "$url" >> "$LOG" 2>&1; then
      echo "✅ SUCCESS (singularity): $outfile ($(du -sh "$outfile" | cut -f1))"
      SUCCESS=$((SUCCESS + 1))
    else
      echo "❌ FAILED: $outfile"
      rm -f "$outfile" 2>/dev/null
      FAILED=$((FAILED + 1))
    fi
  fi
}

echo "========================================"
echo "OmniBioAI SIF Retry Build (Galaxy depot, resolved tags)"
echo "Started: $START_TIME"
echo "========================================"

## ── Resolved Galaxy depot matches (89) ──────────────────
build_sif "abricate" "https://depot.galaxyproject.org/singularity/abricate:1.0.1--ha8f3691_2"  # same_version
build_sif "alevin" "https://depot.galaxyproject.org/singularity/salmon:1.10.3--hecfa306_0"  # same_version
build_sif "alevinqc" "https://depot.galaxyproject.org/singularity/bioconductor-alevinqc:1.16.0--r43hf17093f_0"  # same_version
build_sif "baypass" "https://depot.galaxyproject.org/singularity/baypass:3.1--h8d36177_0"  # diff_version
build_sif "biscuit" "https://depot.galaxyproject.org/singularity/biscuit:1.9.0.20260624--hdf5d79d_0"  # diff_version
build_sif "camera" "https://depot.galaxyproject.org/singularity/bioconductor-camera:1.56.0--r43ha9d7317_0"  # same_version
build_sif "centrifuge" "https://depot.galaxyproject.org/singularity/centrifuge:1.0.4.1--hdcf5f25_2"  # same_version
build_sif "chembl_tools" "https://depot.galaxyproject.org/singularity/chembl_webresource_client:0.9.31"  # diff_version
build_sif "circexplorer2" "https://depot.galaxyproject.org/singularity/circexplorer2:2.3.8--pyh864c0ab_1"  # diff_version
build_sif "ciri2_v2" "https://depot.galaxyproject.org/singularity/ciri2:2.0.6--pl5321hdfd78af_0"  # same_version
build_sif "clipper_v2" "https://depot.galaxyproject.org/singularity/clipper:2.1.20180802--hb2a3317_1"  # diff_version
build_sif "cutesv" "https://depot.galaxyproject.org/singularity/cutesv:2.1.0--pyhdfd78af_0"  # same_version
build_sif "deeplc" "https://depot.galaxyproject.org/singularity/deeplc:3.1.9--pyhdfd78af_0"  # diff_version
build_sif "f5c" "https://depot.galaxyproject.org/singularity/f5c:1.3--h500492e_0"  # same_version
build_sif "find_circ" "https://depot.galaxyproject.org/singularity/find_circ:1.2--hdfd78af_0"  # same_version
build_sif "finestructure" "https://depot.galaxyproject.org/singularity/finestructure:4.1.1--pl5321hdfd78af_0"  # same_version
build_sif "fpocket_v2" "https://depot.galaxyproject.org/singularity/fpocket:4.0.0"  # diff_version
build_sif "gmap" "https://depot.galaxyproject.org/singularity/gmap:2025.07.31--pl5321hb1d24b7_1"  # diff_version
build_sif "gnps" "https://depot.galaxyproject.org/singularity/matchms:0.24.0--pyhdfd78af_1"  # same_version
build_sif "gopeaks" "https://depot.galaxyproject.org/singularity/gopeaks:1.0.0--hf05dbd8_1"  # same_version
build_sif "great" "https://depot.galaxyproject.org/singularity/bioconductor-rgreat:2.2.0--r43hf17093f_0"  # same_version
build_sif "gubbins" "https://depot.galaxyproject.org/singularity/gubbins:3.3.5--py39pl5321he4a0461_0"  # same_version
build_sif "hapflk" "https://depot.galaxyproject.org/singularity/hapflk:1.3.0--py27hae1dfa2_2"  # diff_version
build_sif "icount" "https://depot.galaxyproject.org/singularity/icount-mini:2.0.3--pyh5e36f6f_0"  # same_version
build_sif "idr" "https://depot.galaxyproject.org/singularity/idr:2.0.4.2--py39hec7c8de_9"  # same_version
build_sif "igblast" "https://depot.galaxyproject.org/singularity/igblast:1.22.0--pl5321h6a68c12_0"  # same_version
build_sif "im2deep" "https://depot.galaxyproject.org/singularity/im2deep:1.2.0--pyhdfd78af_0"  # diff_version
build_sif "jasmine" "https://depot.galaxyproject.org/singularity/jasmine:1.1--hdfd78af_1"  # diff_version
build_sif "kallisto_v2" "https://depot.galaxyproject.org/singularity/kallisto:0.50.1--hc877fd6_1"  # same_version
build_sif "kleborate" "https://depot.galaxyproject.org/singularity/kleborate:3.2.4--pyhdfd78af_0"  # diff_version
build_sif "last" "https://depot.galaxyproject.org/singularity/last:992--h8b12597_0"  # diff_version
build_sif "lofreq" "https://depot.galaxyproject.org/singularity/lofreq:2.1.5--py39hf2bf078_8"  # same_version
build_sif "macs3" "https://depot.galaxyproject.org/singularity/macs3:3.0.4--py313hda738de_0"  # diff_version
build_sif "mdanalysis" "https://depot.galaxyproject.org/singularity/mdanalysis:1.0.0"  # diff_version
build_sif "metfrag" "https://depot.galaxyproject.org/singularity/metfrag:2.4.5--hdfd78af_5"  # diff_version
build_sif "mhcflurry" "https://depot.galaxyproject.org/singularity/mhcflurry:2.1.0--pyh7cba7a3_0"  # same_version
build_sif "mirdeep2_v2" "https://depot.galaxyproject.org/singularity/mirdeep2:2.0.1.3--hdfd78af_2"  # same_version
build_sif "mordred" "https://depot.galaxyproject.org/singularity/mordred:1.2.0--2"  # same_version
build_sif "ms2pip" "https://depot.galaxyproject.org/singularity/ms2pip:3.11.0--py39hec7c8de_2"  # same_version
build_sif "multibamsummary" "https://depot.galaxyproject.org/singularity/deeptools:3.5.4--pyhdfd78af_1"  # same_version
build_sif "mzmine_v2" "https://depot.galaxyproject.org/singularity/mzmine:4.7.8--hdfd78af_0"  # diff_version
build_sif "nanopolish" "https://depot.galaxyproject.org/singularity/nanopolish:0.14.0--hee927d3_5"  # same_version
build_sif "ngmlr" "https://depot.galaxyproject.org/singularity/ngmlr:0.2.7--he941832_0"  # same_version
build_sif "novoalign" "https://depot.galaxyproject.org/singularity/novoalign:4.03.04--h5ca1c30_4"  # diff_version
build_sif "openbabel" "https://depot.galaxyproject.org/singularity/openbabel:3.1.1--2"  # same_version
build_sif "pblat" "https://depot.galaxyproject.org/singularity/pblat:2.5.1--h84c94e8_3"  # same_version
build_sif "peakachu" "https://depot.galaxyproject.org/singularity/peakachu:0.2.0--py38he5da3d1_3"  # diff_version
build_sif "peka" "https://depot.galaxyproject.org/singularity/peka:1.0.2--pyhdfd78af_0"  # diff_version
build_sif "peptideshaker" "https://depot.galaxyproject.org/singularity/peptide-shaker:3.0.8--hdfd78af_0"  # diff_version
build_sif "phantompeakqualtools" "https://depot.galaxyproject.org/singularity/phantompeakqualtools:1.2.2--hdfd78af_1"  # same_version
build_sif "pindel" "https://depot.galaxyproject.org/singularity/pindel:0.2.5b9--htslib1.7_1"  # same_version
build_sif "piranha_v2" "https://depot.galaxyproject.org/singularity/piranha:1.2.1--ha5748cb_11"  # same_version
build_sif "pisces" "https://depot.galaxyproject.org/singularity/pisces:5.2.9.122--0"  # diff_version
build_sif "platypus" "https://depot.galaxyproject.org/singularity/platypus-variant:0.8.1.1--py27hdbffeaa_3"  # same_version
build_sif "pmp" "https://depot.galaxyproject.org/singularity/bioconductor-pmp:1.12.0--r43hdfd78af_0"  # same_version
build_sif "poppunk" "https://depot.galaxyproject.org/singularity/poppunk:2.6.2--py39hd8cb238_0"  # same_version
build_sif "pureclip" "https://depot.galaxyproject.org/singularity/pureclip:1.3.1--r44h9ee0642_2"  # same_version
build_sif "rdkit" "https://depot.galaxyproject.org/singularity/rdkit:2021.03.4"  # diff_version
build_sif "resfinder" "https://depot.galaxyproject.org/singularity/resfinder:4.7.2--pyhdfd78af_0"  # diff_version
build_sif "roary" "https://depot.galaxyproject.org/singularity/roary:3.13.0--pl526h516909a_0"  # same_version
build_sif "scater" "https://depot.galaxyproject.org/singularity/bioconductor-scater:1.28.0--r43hdfd78af_0"  # same_version
build_sif "scds" "https://depot.galaxyproject.org/singularity/bioconductor-scds:1.16.0--r43hdfd78af_0"  # same_version
build_sif "scirpy" "https://depot.galaxyproject.org/singularity/scirpy:0.16.1--pyhdfd78af_0"  # same_version
build_sif "scmap" "https://depot.galaxyproject.org/singularity/bioconductor-scmap:1.8.0--r36he1b5a44_0"  # diff_version
build_sif "scran" "https://depot.galaxyproject.org/singularity/bioconductor-scran:1.8.4--r351hfc679d8_0"  # diff_version
build_sif "scvi_v2" "https://depot.galaxyproject.org/singularity/scvi-tools:0.9.1--py_0"  # diff_version
build_sif "seacr" "https://depot.galaxyproject.org/singularity/seacr:1.3--hdfd78af_2"  # same_version
build_sif "segemehl" "https://depot.galaxyproject.org/singularity/segemehl:0.3.4--hfe57441_9"  # same_version
build_sif "shortstack" "https://depot.galaxyproject.org/singularity/shortstack:4.0.3--hdfd78af_0"  # same_version
build_sif "slingshot" "https://depot.galaxyproject.org/singularity/bioconductor-slingshot:2.8.0--r43hdfd78af_0"  # same_version
build_sif "smina" "https://depot.galaxyproject.org/singularity/smina:2017.11.9--0"  # diff_version
build_sif "snap" "https://depot.galaxyproject.org/singularity/snap-aligner:2.0.3--hdcf5f25_3"  # same_version
build_sif "solo_v2" "https://depot.galaxyproject.org/singularity/scvi-tools:0.9.1--py_0"  # diff_version
build_sif "stride" "https://depot.galaxyproject.org/singularity/stride:1.0--hd28b015_4"  # diff_version
build_sif "suppa2" "https://depot.galaxyproject.org/singularity/suppa:2.3--py_2"  # same_version
build_sif "survivor" "https://depot.galaxyproject.org/singularity/survivor:1.0.7--he513fc3_0"  # same_version
build_sif "svim" "https://depot.galaxyproject.org/singularity/svim:2.0.0--pyhdfd78af_0"  # same_version
build_sif "talon_v2" "https://depot.galaxyproject.org/singularity/talon:5.0--pyhdfd78af_0"  # same_version
build_sif "tracer" "https://depot.galaxyproject.org/singularity/tracer:1.7.2--hdfd78af_0"  # diff_version
build_sif "tradeseq" "https://depot.galaxyproject.org/singularity/bioconductor-tradeseq:1.14.0--r43hdfd78af_0"  # same_version
build_sif "transit" "https://depot.galaxyproject.org/singularity/transit:3.3.20--pyhdfd78af_0"  # diff_version
build_sif "treemix" "https://depot.galaxyproject.org/singularity/treemix:1.13--hffe338e_4"  # same_version
build_sif "trust4_v2" "https://depot.galaxyproject.org/singularity/trust4:1.0.9--h5b5514e_0"  # same_version
build_sif "truvari" "https://depot.galaxyproject.org/singularity/truvari:4.2.2--pyhdfd78af_0"  # same_version
build_sif "varscan2" "https://depot.galaxyproject.org/singularity/varscan:2.4.4--hdfd78af_1"  # same_version
build_sif "verse" "https://depot.galaxyproject.org/singularity/verse:0.1.5--hed695b0_4"  # same_version
build_sif "vina" "https://depot.galaxyproject.org/singularity/autodock-vina:1.1.2--h9ee0642_3"  # diff_version
build_sif "vina_v2" "https://depot.galaxyproject.org/singularity/autodock-vina:1.1.2--h9ee0642_3"  # diff_version
build_sif "whamg" "https://depot.galaxyproject.org/singularity/wham:1.8.0.1.2017.05.03--hd28b015_0"  # diff_version

## ── Summary ──────────────────────────────────────────────
echo ""
echo "========================================"
echo "RETRY (DEPOT) BUILD COMPLETE"
echo "Started: $START_TIME"
echo "Finished: $(date)"
echo "✅ Success: $SUCCESS"
echo "❌ Failed:  $FAILED"
echo "⏭️  Skipped: $SKIPPED"
echo ""
echo "Total SIF images now:"
ls ~/Desktop/machine/omnibioai-tool-images/sif/*.sif | wc -l
echo "========================================"
