#!/usr/bin/env bash
# build_remaining.sh — Build all 168 remaining ARM64 SIF images
set -euo pipefail

SIF_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/sif
DF_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/dockerfiles
LOG_DIR=/home/manish/Desktop/machine/omnibioai-tool-images/build_logs
mkdir -p "$LOG_DIR"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

TOTAL=168
BUILT=0; FAILED=0; SKIPPED=0

build_tool() {
  local name=$1 idx=$2
  local sif="${SIF_DIR}/${name}_arm64.sif"
  local log="${LOG_DIR}/${name}.log"
  local df="${DF_DIR}/Dockerfile.${name}"

  if [ -f "$sif" ]; then
    echo -e "${YELLOW}SKIP${NC}  [$idx/$TOTAL] ${name} (SIF exists)"
    ((SKIPPED++)) || true; return 0
  fi
  if [ ! -f "$df" ]; then
    echo -e "${RED}MISS${NC}  [$idx/$TOTAL] ${name} (no Dockerfile)"
    ((FAILED++)) || true; return 1
  fi

  local tag="${name,,}"
  echo -e "${YELLOW}BUILD${NC} [$idx/$TOTAL] ${name}..."
  if docker build --platform linux/arm64 -t "${tag}:latest" -f "$df" . > "$log" 2>&1; then
    if singularity build "$sif" "docker-daemon://${tag}:latest" >> "$log" 2>&1; then
      echo -e "${GREEN}OK${NC}    [$idx/$TOTAL] ${name}"
      ((BUILT++)) || true
    else
      echo -e "${RED}FAIL${NC}  [$idx/$TOTAL] ${name} (singularity build failed — see $log)"
      ((FAILED++)) || true
    fi
  else
    echo -e "${RED}FAIL${NC}  [$idx/$TOTAL] ${name} (docker build failed — see $log)"
    ((FAILED++)) || true
  fi
}

echo "=== OmniBioAI remaining-168 builder — $(date) ==="
echo "Building 168 remaining SIFs (skips already-built ones)"
echo

build_tool admixture_ancestry 1
build_tool alphafold2_monomer 2
build_tool alphafold2_multimer 3
build_tool alphamissense 4
build_tool amber 5
build_tool arcashla 6
build_tool atac_pipeline 7
build_tool autodock_gpu 8
build_tool base_with_runner 9
build_tool baysor 10
build_tool bbtools 11
build_tool beagle5_impute 12
build_tool boltz 13
build_tool braker3_annotation 14
build_tool bsmap 15
build_tool cactus_alignment 16
build_tool canu_assembly 17
build_tool cell2location 18
build_tool cell2location_deconv 19
build_tool chai1 20
build_tool checkm2 21
build_tool ciri2 22
build_tool clipper 23
build_tool cobalt 24
build_tool colabfold 25
build_tool cutrun 26
build_tool dastool 27
build_tool deepfri_predict 28
build_tool diann 29
build_tool diffdock 30
build_tool dorado 31
build_tool dorado_basecall_adv 32
build_tool eager2 33
build_tool equibind 34
build_tool esm3 35
build_tool facets 36
build_tool fastqscreen 37
build_tool faststructure 38
build_tool flames 39
build_tool flare_ancestry 40
build_tool foldseek 41
build_tool fpocket 42
build_tool fragpipe 43
build_tool freesasa 44
build_tool gatk_filter 45
build_tool gatk_haplotype_caller 46
build_tool gatk_realign 47
build_tool genomic_fm 48
build_tool gfatools_convert 49
build_tool giotto 50
build_tool gnina 51
build_tool gridss 52
build_tool grnboost2 53
build_tool gsea_preranked 54
build_tool guppy 55
build_tool guppy_basecall_adv 56
build_tool hdxms 57
build_tool hicpro 58
build_tool hifiasm 59
build_tool hifiasm_assembly_adv 60
build_tool hint 61
build_tool hrdetect_hrd 62
build_tool impute5 63
build_tool intervar 64
build_tool kaiju 65
build_tool ldsc_heritability 66
build_tool lefse 67
build_tool liger 68
build_tool lumpy_sv 69
build_tool mageckvispr_screen 70
build_tool masurca 71
build_tool maxbin2_binning 72
build_tool mcmicro 73
build_tool mcscan_synteny 74
build_tool megahit 75
build_tool meme 76
build_tool memechip 77
build_tool merip 78
build_tool merqury_eval 79
build_tool metabat2 80
build_tool metaboanalystr 81
build_tool metamorpheus 82
build_tool methyldackel 83
build_tool methylkit_dmp 84
build_tool milo 85
build_tool minigraph_align 86
build_tool minimap2_adv 87
build_tool mirdeep2 88
build_tool mixcr 89
build_tool mixomics_analysis 90
build_tool mofa 91
build_tool mol_gnn 92
build_tool monocle3_trajectory_adv 93
build_tool mothur 94
build_tool msfragger 95
build_tool msisensor2 96
build_tool msiseq 97
build_tool msmc2_demography 98
build_tool msmutect_ms 99
build_tool mutect2_somatic 100
build_tool mzmine 101
build_tool nextdenovo_assembly 102
build_tool nfcore_nanoseq 103
build_tool odgi_pangenome 104
build_tool openms 105
build_tool opera_ms_assembly 106
build_tool optitype 107
build_tool pggb_pangenome 108
build_tool phenix 109
build_tool picrust2 110
build_tool polyphen2 111
build_tool progeny 112
build_tool proteinmpnn 113
build_tool prsice2 114
build_tool psmc_demography 115
build_tool purple 116
build_tool pvacseq 117
build_tool pytorch_train_adv 118
build_tool qiime2 119
build_tool qualimap 120
build_tool rapids_analysis 121
build_tool raven_assembly 122
build_tool rctd 123
build_tool relion 124
build_tool rfdiffusion 125
build_tool rfmix_ancestry 126
build_tool rosettafold 127
build_tool scbridge 128
build_tool scdiffusion 129
build_tool scglue 130
build_tool scgpt 131
build_tool schmutzi 132
build_tool sctype 133
build_tool scvi 134
build_tool seqwish_graph 135
build_tool seurat_cluster_adv 136
build_tool shapeit5 137
build_tool sirius 138
build_tool smartpca_pca 139
build_tool smoothxg_graph 140
build_tool snapatac2 141
build_tool spaceranger 142
build_tool spaceranger_count_adv 143
build_tool spades_assembly_adv 144
build_tool spliceai 145
build_tool spotlight 146
build_tool starfusion 147
build_tool sweed 148
build_tool talon_transcriptome 149
build_tool template 150
build_tool tensorflow_train_adv 151
build_tool titan_cn 152
build_tool tmalign 153
build_tool tobias 154
build_tool totalvi 155
build_tool trinity_assembly_adv 156
build_tool trust4 157
build_tool trycycler_consensus 158
build_tool unicycler_hybrid 159
build_tool vae_train_adv 160
build_tool vamb_binning 161
build_tool velocyto 162
build_tool verkko 163
build_tool vg_construct 164
build_tool virsorter2 165
build_tool xcms 166
build_tool xenium 167
build_tool xlinkx 168

echo
echo "=== Build complete — $(date) ==="
echo -e "  ${GREEN}Built:${NC}   $BUILT"
echo -e "  ${YELLOW}Skipped:${NC} $SKIPPED"
echo -e "  ${RED}Failed:${NC}  $FAILED"
