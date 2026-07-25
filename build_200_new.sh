#!/bin/bash
cd ~/Desktop/machine/omnibioai-tool-images

for tool_pkg in \
  "bwa_mem2:bwa-mem2" \
  "last_align:last" \
  "mummer4:mummer4" \
  "ngmlr:ngmlr" \
  "pbmm2:pbmm2" \
  "winnowmap:winnowmap" \
  "chromap:chromap" \
  "gmap:gmap" \
  "samblaster:samblaster" \
  "biobambam2:biobambam" \
  "meryl:meryl" \
  "lastz:lastz" \
  "segemehl:segemehl" \
  "gem3_mapper:gem3-mapper" \
  "subread:subread" \
  "yara:yara" \
  "bowtie1:bowtie" \
  "wtdbg2:wtdbg2" \
  "vcfanno:vcfanno" \
  "snpsift:snpsift" \
  "vt:vt" \
  "vcf2maf:vcf2maf" \
  "maftools:bioconductor-maftools" \
  "vardict:vardict-java" \
  "varscan2:varscan" \
  "lofreq:lofreq" \
  "somaticsniper:somatic-sniper" \
  "svtyper:svtyper" \
  "duphold:duphold" \
  "smoove:smoove" \
  "truvari:truvari" \
  "annotsv:annotsv" \
  "gangstr:gangstr" \
  "expansionhunter:expansionhunter" \
  "hipstr:hipstr" \
  "varlociraptor:varlociraptor" \
  "breakdancer:breakdancer" \
  "deepsomatic:deepsomatic" \
  "ballgown:bioconductor-ballgown" \
  "rnaseqc:rna-seqc" \
  "qorts:qorts" \
  "fusioncatcher:fusioncatcher" \
  "jaffa:jaffa" \
  "squid_fusion:squid" \
  "alevin_fry:alevin-fry" \
  "bustools:bustools" \
  "kb_python:kb-python" \
  "irfinder:irfinder" \
  "suppa2:suppa" \
  "whippet:whippet" \
  "drimseq:bioconductor-drimseq" \
  "dupradar:bioconductor-dupradar" \
  "flair:flair" \
  "pychopper:pychopper" \
  "rmats2sashimiplot:rmats2sashimiplot" \
  "salmontools:salmontools" \
  "spladder:spladder" \
  "outrigger:outrigger" \
  "cellphonedb:cellphonedb" \
  "liana:liana" \
  "palantir:palantir" \
  "phate:phate" \
  "opentsne:opentsne" \
  "scdblfinder:bioconductor-scdblfinder" \
  "celloracle:celloracle" \
  "cytoflow:cytoflow" \
  "starsolo:star" \
  "cellcharter:cellcharter" \
  "stereoscope:stereoscope" \
  "cellsnp_lite:cellsnp-lite" \
  "vireosnp:vireosnp" \
  "souporcell:souporcell" \
  "stardist:stardist" \
  "destiny:bioconductor-destiny" \
  "voyager:bioconductor-voyager" \
  "pycistopic:pycistopic" \
  "diffxpy:diffxpy" \
  "bento_tools:bento-tools" \
  "juicer:juicer" \
  "genrich:genrich" \
  "seacr:seacr" \
  "gopeaks:gopeaks" \
  "danpos:danpos3" \
  "ataqv:ataqv" \
  "bwameth:bwameth" \
  "methylpy:methylpy" \
  "preseq:preseq" \
  "phantompeakqualtools:phantompeakqualtools" \
  "fithic:fithic" \
  "hic_straw:hic-straw" \
  "hicup:hicup" \
  "diffhic:bioconductor-diffhic" \
  "multihiccompare:bioconductor-multihiccompare" \
  "nucleoatac:nucleoatac" \
  "rgt:rgt" \
  "sicer2:sicer2" \
  "gem_peakcaller:gem" \
  "genomation:bioconductor-genomation" \
  "anvio:anvio" \
  "vsearch:vsearch" \
  "breseq:breseq" \
  "bakta:bakta" \
  "roary:roary" \
  "panaroo:panaroo" \
  "abricate:abricate" \
  "rgi_card:rgi" \
  "amrfinderplus:ncbi-amrfinderplus" \
  "mlst:mlst" \
  "snippy:snippy" \
  "gubbins:gubbins" \
  "metawrap:metawrap-mg" \
  "instrain:instrain" \
  "checkv:checkv" \
  "phyloflash:phyloflash" \
  "deblur:deblur" \
  "metaquast:quast" \
  "kraken_biom:kraken-biom" \
  "phigaro:phigaro" \
  "comet_ms:comet-ms" \
  "msstats:bioconductor-msstats" \
  "pyopenms:pyopenms" \
  "matchms:matchms" \
  "casanovo:casanovo" \
  "encyclopedia:encyclopedia" \
  "msnbase:bioconductor-msnbase" \
  "protti:bioconductor-protti" \
  "ms2rescore:ms2rescore" \
  "asari:asari" \
  "xtandem:xtandem" \
  "crux_toolkit:crux-toolkit" \
  "pyteomics:pyteomics" \
  "msqrob2:bioconductor-msqrob2" \
  "peptideshaker:peptide-shaker" \
  "searchgui:searchgui" \
  "metfrag:metfrag-cli" \
  "maldiquant:bioconductor-maldiquant" \
  "openfold:openfold" \
  "omegafold:omegafold" \
  "mdanalysis:mdanalysis" \
  "mdtraj:mdtraj" \
  "rdkit:rdkit" \
  "openbabel:openbabel" \
  "vina:autodock-vina" \
  "smina:smina" \
  "plip:plip" \
  "meeko:meeko" \
  "biotite:biotite" \
  "prody:prody" \
  "igfold:igfold" \
  "ambertools:ambertools" \
  "biopython:biopython" \
  "packmol:packmol" \
  "pytraj:pytraj" \
  "pdb2pqr:pdb2pqr" \
  "apbs:apbs" \
  "biopandas:biopandas" \
  "lightgbm:lightgbm" \
  "catboost:catboost" \
  "statsmodels:statsmodels" \
  "hdbscan:hdbscan" \
  "onnxruntime:onnxruntime" \
  "accelerate:accelerate" \
  "peft:peft" \
  "bitsandbytes:bitsandbytes" \
  "fastai:fastai" \
  "tpot:tpot" \
  "imbalanced_learn:imbalanced-learn" \
  "torchdrug:torchdrug" \
  "torchani:torchani" \
  "e3nn:e3nn" \
  "keras:keras" \
  "tensorboard:tensorboard" \
  "skbio:scikit-bio" \
  "opacus:opacus" \
  "gensim:gensim" \
  "scispacy:scispacy" \
  "jellyfish:kmer-jellyfish" \
  "kmc:kmc" \
  "genomescope2:genomescope2" \
  "gfastats:gfastats" \
  "assembly_stats:assembly-stats" \
  "pigz:pigz" \
  "sra_tools:sra-tools" \
  "entrez_direct:entrez-direct" \
  "ncbi_datasets_cli:ncbi-datasets-cli" \
  "pyfaidx:pyfaidx" \
  "gffutils:gffutils" \
  "cwltool:cwltool" \
  "samstat:samstat" \
  "fastqe:fastqe" \
  "nanofilt:nanofilt" \
  "bx_python:bx-python" \
  "toil:toil" \
  "datamash:datamash" \
  "parallel_gnu:parallel" \
  "igv_reports:igv-reports" \
  ; do

  tool="${tool_pkg%%:*}"
  pkg="${tool_pkg##*:}"

  if [ -f "sif/${tool}_arm64.sif" ]; then
    echo "⏭️  SKIP: $tool"
    continue
  fi

  cat > dockerfiles/Dockerfile.$tool << DEOF
FROM mambaorg/micromamba:1.5.8
RUN micromamba install -y -n base -c bioconda -c conda-forge \
    $pkg && micromamba clean --all --yes
DEOF

  echo "🔨 Building $tool..."
  docker build --no-cache \
    -f dockerfiles/Dockerfile.$tool \
    -t ${tool}:latest . 2>&1 | tail -2
  if [ ${PIPESTATUS[0]} -eq 0 ]; then
    singularity build \
      sif/${tool}_arm64.sif \
      docker-daemon://${tool}:latest 2>&1 | tail -2
    if [ ${PIPESTATUS[0]} -eq 0 ]; then
      count=$(ls sif/*.sif | wc -l)
      echo "✅ $tool! SIFs: $count"
    else
      echo "❌ $tool failed (singularity build)"
    fi
  else
    echo "❌ $tool failed (docker build)"
  fi

  free_ram=$(free -m | grep Mem | awk '{print $7}')
  if [ $free_ram -lt 5000 ]; then
    echo "⚠️ RAM low - stopping"
    break
  fi
done

echo "🎉 Final: $(ls sif/*.sif | wc -l) SIFs"
