#!/bin/bash
set -e
SIF_DIR=~/Desktop/machine/omnibioai-tool-images/sif
LOG=~/Desktop/machine/omnibioai-tool-images/build_logs/build_200_$(date +%Y%m%d).log
mkdir -p ~/Desktop/machine/omnibioai-tool-images/build_logs
cd $SIF_DIR

SUCCESS=0
FAILED=0
SKIPPED=0
START_TIME=$(date)

build_sif() {
  local name=$1
  local source=$2
  local outfile="${name}_arm64.sif"

  if [ -f "$outfile" ]; then
    echo "⏭️  SKIP: $outfile already exists"
    SKIPPED=$((SKIPPED + 1))
    return
  fi

  echo "🔨 Building: $outfile from $source"
  if singularity pull --arch arm64 --name "$outfile" "$source" >> $LOG 2>&1; then
    echo "✅ SUCCESS: $outfile ($(du -sh $outfile | cut -f1))"
    SUCCESS=$((SUCCESS + 1))
  else
    echo "❌ FAILED: $outfile"
    FAILED=$((FAILED + 1))
    rm -f "$outfile" 2>/dev/null
  fi
}

echo "========================================"
echo "OmniBioAI SIF Build - 195 new images"
echo "Started: $(date)"
echo "========================================"

## ── Alignment (15) ──────────────────────────────────────
build_sif "bwa_mem2" "docker://quay.io/biocontainers/bwa-mem2:2.2.1--hd03093a_5"
build_sif "ngmlr" "docker://quay.io/biocontainers/ngmlr:0.2.7--h7b1c576_7"
build_sif "pbmm2" "docker://quay.io/biocontainers/pbmm2:1.13.1--h9ee0642_0"
build_sif "last" "docker://quay.io/biocontainers/last:1394--pl5321h031d066_1"
build_sif "stampy" "docker://quay.io/biocontainers/stampy:1.0.32--py39h9a82719_4"
build_sif "subread" "docker://quay.io/biocontainers/subread:2.0.6--he4a0461_0"
build_sif "tophat2" "docker://quay.io/biocontainers/tophat:2.1.1--py27_3"
build_sif "segemehl" "docker://quay.io/biocontainers/segemehl:0.3.4--h7b1c576_8"
build_sif "blat" "docker://quay.io/biocontainers/blat:36--0"
build_sif "gmap" "docker://quay.io/biocontainers/gmap:2023.04.20--pl5321hd03093a_0"
build_sif "pblat" "docker://quay.io/biocontainers/pblat:2.5.1--hd03093a_0"
build_sif "bbmap" "docker://quay.io/biocontainers/bbmap:39.01--h5c4e2a8_0"
build_sif "novoalign" "docker://quay.io/biocontainers/novoalign:4.03.01--h87f3376_0"
build_sif "minimap2_hifi" "docker://quay.io/biocontainers/minimap2:2.26--he4a0461_2"
build_sif "snap" "docker://quay.io/biocontainers/snap-aligner:2.0.3--h9a82719_0"

## ── RNA Analysis (20) ────────────────────────────────────
build_sif "alevin" "docker://quay.io/biocontainers/salmon:1.10.3--h9d3e62a_2"
build_sif "verse" "docker://quay.io/biocontainers/verse:0.1.5--h4a94de4_4"
build_sif "suppa2" "docker://quay.io/biocontainers/suppa:2.3--py39h5371cbf_3"
build_sif "vast_tools" "docker://quay.io/biocontainers/vast-tools:2.5.1--hdfd78af_2"
build_sif "whippet" "docker://quay.io/biocontainers/whippet:1.6.1--julia_0"
build_sif "circexplorer2" "docker://quay.io/biocontainers/circexplorer2:3.3.6--py_0"
build_sif "find_circ" "docker://quay.io/biocontainers/find_circ:1.2--py27h9801fc8_5"
build_sif "ciri2_v2" "docker://quay.io/biocontainers/ciri2:2.0.6--hdfd78af_1"
build_sif "mirdeep2_v2" "docker://quay.io/biocontainers/mirdeep2:2.0.1.3--pl5321hd03093a_1"
build_sif "shortstack" "docker://quay.io/biocontainers/shortstack:4.0.3--py310hdfd78af_0"
build_sif "piranha_v2" "docker://quay.io/biocontainers/piranha:1.2.1--hd03093a_4"
build_sif "clipper_v2" "docker://quay.io/biocontainers/clipper:3.0.0--py38h7be5676_2"
build_sif "pureclip" "docker://quay.io/biocontainers/pureclip:1.3.1--h9ee0642_4"
build_sif "dewseq" "docker://quay.io/biocontainers/bioconductor-dewseq:1.14.0--r43hdfd78af_0"
build_sif "peka" "docker://quay.io/biocontainers/peka:1.0.5--py310hdfd78af_0"
build_sif "icount" "docker://quay.io/biocontainers/icount-mini:2.0.3--py_0"
build_sif "transit" "docker://quay.io/biocontainers/transit:3.2.6--py39h5371cbf_0"
build_sif "kallisto_v2" "docker://quay.io/biocontainers/kallisto:0.50.1--ha4fb952_1"
build_sif "omniclip" "docker://quay.io/biocontainers/omniclip:0.1--py_0"
build_sif "talon_v2" "docker://quay.io/biocontainers/talon:5.0--py_0"

## ── Variant Calling (20) ─────────────────────────────────
build_sif "varscan2" "docker://quay.io/biocontainers/varscan:2.4.4--hdfd78af_3"
build_sif "lofreq" "docker://quay.io/biocontainers/lofreq:2.1.5--py39hbcfb28b_8"
build_sif "vardict" "docker://quay.io/biocontainers/vardict-java:1.8.3--hdfd78af_0"
build_sif "platypus" "docker://quay.io/biocontainers/platypus-variant:0.8.1.1--py27h9801fc8_3"
build_sif "pindel" "docker://quay.io/biocontainers/pindel:0.2.5b9--hd03093a_3"
build_sif "lancet" "docker://quay.io/biocontainers/lancet:1.1.0--h9ee0642_2"
build_sif "clairs" "docker://hkubal/clairs:v0.1.7"
build_sif "pepper" "docker://kishwars/pepper_deepvariant:r0.8"
build_sif "nanosv" "docker://quay.io/biocontainers/nanosv:1.2.4--py_0"
build_sif "pbsv" "docker://quay.io/biocontainers/pbsv:2.9.0--h9ee0642_0"
build_sif "cutesv" "docker://quay.io/biocontainers/cutesv:2.1.0--py39hdfd78af_0"
build_sif "svim" "docker://quay.io/biocontainers/svim:2.0.0--py_0"
build_sif "survivor" "docker://quay.io/biocontainers/survivor:1.0.7--h9f5acd7_2"
build_sif "jasmine" "docker://quay.io/biocontainers/jasmine:1.1.5--hdfd78af_0"
build_sif "truvari" "docker://quay.io/biocontainers/truvari:4.2.2--py310hdfd78af_0"
build_sif "smoove" "docker://brentp/smoove:v0.2.8"
build_sif "whamg" "docker://quay.io/biocontainers/wham:1.8.0b02--hd03093a_4"
build_sif "pisces" "docker://quay.io/biocontainers/pisces:5.3.0.0--0"
build_sif "parliament2" "docker://dnanexus/parliament2:latest"
build_sif "haplotypecaller" "docker://broadinstitute/gatk:4.5.0.0"

## ── Epigenomics (15) ─────────────────────────────────────
build_sif "macs3" "docker://quay.io/biocontainers/macs3:3.0.0b3--py39h5371cbf_0"
build_sif "gem" "docker://quay.io/biocontainers/gem:3.4--hdfd78af_2"
build_sif "peakachu" "docker://quay.io/biocontainers/peakachu:2.1--py39hdfd78af_0"
build_sif "seacr" "docker://quay.io/biocontainers/seacr:1.3--hdfd78af_3"
build_sif "gopeaks" "docker://quay.io/biocontainers/gopeaks:1.0.0--h9f5acd7_0"
build_sif "great" "docker://quay.io/biocontainers/bioconductor-rgreat:2.2.0--r43hdfd78af_0"
build_sif "motifmatchr" "docker://quay.io/biocontainers/bioconductor-motifmatchr:1.22.0--r43hf17093f_0"
build_sif "biscuit" "docker://quay.io/biocontainers/biscuit:1.3.0.20230531--h7b1c576_0"
build_sif "nanopolish" "docker://quay.io/biocontainers/nanopolish:0.14.0--hd56a234_2"
build_sif "f5c" "docker://quay.io/biocontainers/f5c:1.3--h4ac6f70_3"
build_sif "remora" "docker://quay.io/biocontainers/ont-remora:3.0.1--py310h4b81fae_0"
build_sif "deepsignal" "docker://quay.io/biocontainers/deepsignal2:0.1.7--py38h585ceec_0"
build_sif "idr" "docker://quay.io/biocontainers/idr:2.0.4.2--py39h5371cbf_4"
build_sif "phantompeakqualtools" "docker://quay.io/biocontainers/phantompeakqualtools:1.2.2--r43hdfd78af_1"
build_sif "multibamsummary" "docker://quay.io/biocontainers/deeptools:3.5.4--py39h7b1c576_0"

## ── Metagenomics (15) ────────────────────────────────────
build_sif "centrifuge" "docker://quay.io/biocontainers/centrifuge:1.0.4.1--hd03093a_4"
build_sif "mmseqs2" "docker://quay.io/biocontainers/mmseqs2:15.6f452--pl5321h6a68c12_0"
build_sif "bakta" "docker://quay.io/biocontainers/bakta:1.9.3--pyhdfd78af_0"
build_sif "roary" "docker://quay.io/biocontainers/roary:3.13.0--pl5321h516909a_3"
build_sif "panaroo" "docker://quay.io/biocontainers/panaroo:1.3.4--pyhdfd78af_0"
build_sif "poppunk" "docker://quay.io/biocontainers/poppunk:2.6.2--py39h7cff6ad_1"
build_sif "snippy" "docker://quay.io/biocontainers/snippy:4.6.0--hdfd78af_3"
build_sif "gubbins" "docker://quay.io/biocontainers/gubbins:3.3.5--py39h7cff6ad_0"
build_sif "abricate" "docker://quay.io/biocontainers/abricate:1.0.1--hdfd78af_1"
build_sif "amrfinderplus" "docker://quay.io/biocontainers/ncbi-amrfinderplus:3.12.8--h283d18e_0"
build_sif "resfinder" "docker://quay.io/biocontainers/resfinder:4.3.3--py39hdfd78af_0"
build_sif "plasmidfinder" "docker://quay.io/biocontainers/plasmidfinder:2.1.6--hdfd78af_0"
build_sif "snippy_v2" "docker://quay.io/biocontainers/snippy:4.6.0--hdfd78af_3"
build_sif "mlst" "docker://quay.io/biocontainers/mlst:2.23.0--hdfd78af_1"
build_sif "kleborate" "docker://quay.io/biocontainers/kleborate:2.4.1--pyhdfd78af_0"

## ── Structural Biology (15) ──────────────────────────────
build_sif "esmfold" "docker://quay.io/biocontainers/fair-esm:2.0.0--pyhd8ed1ab_0"
build_sif "openfold" "docker://quay.io/biocontainers/openfold:1.0.1--py38h4ccb84a_2"
build_sif "rosettafold2" "docker://quay.io/biocontainers/rosettafold2:1.0--py38h4ccb84a_0"
build_sif "vina" "docker://quay.io/biocontainers/autodock-vina:1.2.5--h23229b4_3"
build_sif "smina" "docker://quay.io/biocontainers/smina:2020.12.10--h9f5acd7_0"
build_sif "ledock" "docker://quay.io/biocontainers/ledock:1.0--0"
build_sif "p2rank" "docker://quay.io/biocontainers/p2rank:2.4.2--hdfd78af_0"
build_sif "naccess" "docker://quay.io/biocontainers/naccess:2.1.1--h031d066_4"
build_sif "dssp" "docker://quay.io/biocontainers/dssp:4.4.1--h4ccb84a_0"
build_sif "stride" "docker://quay.io/biocontainers/stride:2.0--h031d066_3"
build_sif "propka" "docker://quay.io/biocontainers/propka:3.5.0--pyhdfd78af_0"
build_sif "mdanalysis" "docker://quay.io/biocontainers/mdanalysis:2.7.0--py310h4b81fae_0"
build_sif "pdbtools" "docker://quay.io/biocontainers/pdb-tools:2.5.0--pyhdfd78af_0"
build_sif "dogsitescorer" "docker://quay.io/biocontainers/dogsitescorer:2.0--hdfd78af_0"
build_sif "fpocket_v2" "docker://quay.io/biocontainers/fpocket:4.1--h9f5acd7_0"

## ── Population Genetics (10) ─────────────────────────────
build_sif "ldpred2" "docker://quay.io/biocontainers/bioconductor-bigsnpr:1.12.2--r43hf17093f_0"
build_sif "lassosum" "docker://quay.io/biocontainers/lassosum:0.4.5--r43hdfd78af_4"
build_sif "clumpp" "docker://quay.io/biocontainers/clumpp:1.1.2--h9f5acd7_3"
build_sif "treemix" "docker://quay.io/biocontainers/treemix:1.13--h9f5acd7_4"
build_sif "finestructure" "docker://quay.io/biocontainers/finestructure:4.1.1--h9f5acd7_0"
build_sif "chromopainter" "docker://quay.io/biocontainers/chromopainter:2.0--h9f5acd7_0"
build_sif "hapflk" "docker://quay.io/biocontainers/hapflk:1.4--py39h5371cbf_3"
build_sif "rehh" "docker://quay.io/biocontainers/bioconductor-rehh:3.2.2--r43hf17093f_0"
build_sif "selestim" "docker://quay.io/biocontainers/selestim:1.1.6--h9f5acd7_0"
build_sif "baypass" "docker://quay.io/biocontainers/baypass:2.4--h031d066_0"

## ── Proteomics (10) ──────────────────────────────────────
build_sif "proteowizard" "docker://chambm/pwiz-skyline-i-agree-to-the-vendor-licenses:latest"
build_sif "msconvert" "docker://chambm/pwiz-skyline-i-agree-to-the-vendor-licenses:latest"
build_sif "peptideshaker" "docker://quay.io/biocontainers/peptide-shaker:2.2.31--hdfd78af_0"
build_sif "searchgui" "docker://quay.io/biocontainers/searchgui:4.3.3--hdfd78af_0"
build_sif "deepmass" "docker://quay.io/biocontainers/deepmass:1.0.0--py38h4ccb84a_0"
build_sif "ms2pip" "docker://quay.io/biocontainers/ms2pip:3.11.0--py310hdfd78af_0"
build_sif "im2deep" "docker://quay.io/biocontainers/im2deep:0.1.5--py310hdfd78af_0"
build_sif "deeplc" "docker://quay.io/biocontainers/deeplc:2.2.8--py310hdfd78af_0"
build_sif "prosit" "docker://quay.io/biocontainers/ms2ml:0.1.8--py310hdfd78af_0"
build_sif "alphapept_v2" "docker://quay.io/biocontainers/alphapept:0.4.9--py38h4ccb84a_0"

## ── Metabolomics (10) ────────────────────────────────────
build_sif "metfrag" "docker://quay.io/biocontainers/metfrag:2.5.0--hdfd78af_0"
build_sif "msfinder" "docker://quay.io/biocontainers/msfinder:3.5.2--hdfd78af_0"
build_sif "cfmid" "docker://quay.io/biocontainers/cfm-id:4.4.7--hd03093a_1"
build_sif "canopus" "docker://quay.io/biocontainers/sirius:5.8.6--hdfd78af_0"
build_sif "camera" "docker://quay.io/biocontainers/bioconductor-camera:1.56.0--r43hf17093f_0"
build_sif "mummichog" "docker://quay.io/biocontainers/mummichog:2.3.3--py_1"
build_sif "ramclust" "docker://quay.io/biocontainers/bioconductor-ramclustr:1.3.1--r43hf17093f_0"
build_sif "pmp" "docker://quay.io/biocontainers/bioconductor-pmp:1.12.0--r43hf17093f_0"
build_sif "gnps" "docker://quay.io/biocontainers/matchms:0.24.0--pyhd8ed1ab_0"
build_sif "mzmine_v2" "docker://quay.io/biocontainers/mzmine:4.0.0--hdfd78af_0"

## ── Drug Discovery (10) ──────────────────────────────────
build_sif "tankbind" "docker://quay.io/biocontainers/tankbind:1.0.0--py38h4ccb84a_0"
build_sif "rdkit" "docker://quay.io/biocontainers/rdkit:2023.09.5--py310h4b81fae_0"
build_sif "openbabel" "docker://quay.io/biocontainers/openbabel:3.1.1--py310hd1580a4_4"
build_sif "deepchem" "docker://quay.io/biocontainers/deepchem:2.7.1--py39h7cff6ad_0"
build_sif "chemprop" "docker://quay.io/biocontainers/chemprop:1.7.1--py39hdfd78af_0"
build_sif "mordred" "docker://quay.io/biocontainers/mordred:1.2.0--py_1"
build_sif "admetlab" "docker://quay.io/biocontainers/admetlab:2.0--py_0"
build_sif "chembl_tools" "docker://quay.io/biocontainers/chembl_webresource_client:0.10.8--py_0"
build_sif "pkcsm" "docker://quay.io/biocontainers/pkcsm:1.0.0--py_0"
build_sif "vina_v2" "docker://quay.io/biocontainers/autodock-vina:1.2.5--h23229b4_3"

## ── Immunogenomics (10) ──────────────────────────────────
build_sif "igblast" "docker://quay.io/biocontainers/igblast:1.22.0--pl5321h031d066_2"
build_sif "vdjtools" "docker://quay.io/biocontainers/vdjtools:1.2.1--hdfd78af_0"
build_sif "immcantation" "docker://kleinstein/immcantation:4.4.0"
build_sif "bracer" "docker://quay.io/biocontainers/bracer:0.8.3--py_1"
build_sif "tracer" "docker://quay.io/biocontainers/tracer:1.4--py_0"
build_sif "scirpy" "docker://quay.io/biocontainers/scirpy:0.16.1--pyhd8ed1ab_0"
build_sif "dandelion" "docker://quay.io/biocontainers/sc-dandelion:0.3.5--pyhd8ed1ab_0"
build_sif "netmhcpan" "docker://quay.io/biocontainers/netmhcpan:4.1b--hdfd78af_0"
build_sif "mhcflurry" "docker://quay.io/biocontainers/mhcflurry:2.1.0--pyhd8ed1ab_0"
build_sif "trust4_v2" "docker://quay.io/biocontainers/trust4:1.0.9--h43eeafb_0"

## ── ML/DL (10) ───────────────────────────────────────────
build_sif "jax" "docker://quay.io/biocontainers/jax:0.4.20--py310h4b81fae_0"
build_sif "lightning" "docker://quay.io/biocontainers/pytorch-lightning:2.1.3--pyhd8ed1ab_0"
build_sif "wandb" "docker://quay.io/biocontainers/wandb:0.16.6--pyhd8ed1ab_0"
build_sif "optuna" "docker://quay.io/biocontainers/optuna:3.5.0--pyhd8ed1ab_0"
build_sif "dgllife" "docker://quay.io/biocontainers/dgl:1.1.3--py39_0"
build_sif "pyg" "docker://quay.io/biocontainers/pytorch-geometric:2.4.0--py310_0"
build_sif "shap" "docker://quay.io/biocontainers/shap:0.44.1--py310hdfd78af_0"
build_sif "lime" "docker://quay.io/biocontainers/lime:0.2.0.1--py_0"
build_sif "captum" "docker://quay.io/biocontainers/captum:0.7.0--pyhd8ed1ab_0"
build_sif "interpretML" "docker://quay.io/biocontainers/interpret:0.5.1--py310hdfd78af_0"

## ── Single Cell (20) ─────────────────────────────────────
build_sif "cellranger_atac" "docker://quay.io/biocontainers/cellranger-atac:2.1.0--0"
build_sif "starsolo" "docker://quay.io/biocontainers/star:2.7.11b--h43eeafb_0"
build_sif "alevinqc" "docker://quay.io/biocontainers/bioconductor-alevinqc:1.16.0--r43hdfd78af_0"
build_sif "scran" "docker://quay.io/biocontainers/bioconductor-scran:1.28.0--r43hf17093f_0"
build_sif "scater" "docker://quay.io/biocontainers/bioconductor-scater:1.28.0--r43hf17093f_0"
build_sif "slingshot" "docker://quay.io/biocontainers/bioconductor-slingshot:2.8.0--r43hf17093f_0"
build_sif "tradeseq" "docker://quay.io/biocontainers/bioconductor-tradeseq:1.14.0--r43hf17093f_0"
build_sif "scpred" "docker://quay.io/biocontainers/bioconductor-scpred:1.9.2--r43hdfd78af_0"
build_sif "singleR" "docker://quay.io/biocontainers/bioconductor-singler:2.2.0--r43hf17093f_0"
build_sif "garnett" "docker://quay.io/biocontainers/bioconductor-garnett:0.2.22--r43hdfd78af_0"
build_sif "scmap" "docker://quay.io/biocontainers/bioconductor-scmap:1.22.0--r43hf17093f_0"
build_sif "scds" "docker://quay.io/biocontainers/bioconductor-scds:1.16.0--r43hf17093f_0"
build_sif "metacells" "docker://quay.io/biocontainers/metacells:0.9.4--py310hdfd78af_0"
build_sif "pyslingshot" "docker://quay.io/biocontainers/pyslingshot:0.1.0--pyhd8ed1ab_0"
build_sif "cytotrace2" "docker://quay.io/biocontainers/cytotrace2:1.0.0--pyhd8ed1ab_0"
build_sif "wnn_multiome" "docker://quay.io/biocontainers/seurat:5.0.0--r43hf17093f_0"
build_sif "solo_v2" "docker://quay.io/biocontainers/scvi-tools:1.0.4--pyhd8ed1ab_0"
build_sif "scvi_v2" "docker://quay.io/biocontainers/scvi-tools:1.0.4--pyhd8ed1ab_0"
build_sif "istdeco" "docker://quay.io/biocontainers/istdeco:0.1.0--pyhd8ed1ab_0"
build_sif "cardamom" "docker://quay.io/biocontainers/cardamom:0.1.0--pyhd8ed1ab_0"

## ── Summary ──────────────────────────────────────────────
echo ""
echo "========================================"
echo "BUILD COMPLETE"
echo "Started: $START_TIME"
echo "Finished: $(date)"
echo "✅ Success: $SUCCESS"
echo "❌ Failed:  $FAILED"
echo "⏭️  Skipped: $SKIPPED"
echo ""
echo "Total SIF images now:"
ls ~/Desktop/machine/omnibioai-tool-images/sif/*.sif | wc -l
echo "========================================"
