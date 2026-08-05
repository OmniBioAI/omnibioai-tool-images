import asyncio
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

BASE = Path(__file__).parent.parent
DOCKERFILES_DIR = BASE / "dockerfiles"
SIF_DIR = BASE / "sif"
BUILD_LOGS_DIR = BASE / "build_logs"

CATEGORY_MAP: dict[str, str] = {
    # Bio Core
    "fastqc": "Bio Core",
    "multiqc": "Bio Core",
    "trimmomatic": "Bio Core",
    "samtools": "Bio Core",
    "bcftools": "Bio Core",
    "bwa": "Bio Core",
    "star": "Bio Core",
    "featurecounts": "Bio Core",
    "gatk": "Bio Core",
    "deseq2": "Bio Core",
    # Variant Analysis
    "vcftools": "Variant Analysis",
    "snpeff": "Variant Analysis",
    "vep": "Variant Analysis",
    "plink": "Variant Analysis",
    "freebayes": "Variant Analysis",
    "strelka2": "Variant Analysis",
    "annovar": "Variant Analysis",
    # RNA-seq
    "kallisto": "RNA-seq",
    "salmon": "RNA-seq",
    "hisat2": "RNA-seq",
    "stringtie": "RNA-seq",
    "rsem": "RNA-seq",
    "edger": "RNA-seq",
    "limma": "RNA-seq",
    "dexseq": "RNA-seq",
    "rmats": "RNA-seq",
    "tximport": "RNA-seq",
    # Metagenomics
    "kraken2": "Metagenomics",
    "bracken": "Metagenomics",
    "metaphlan": "Metagenomics",
    "humann3": "Metagenomics",
    "diamond": "Metagenomics",
    "prodigal": "Metagenomics",
    "prokka": "Metagenomics",
    "checkm": "Metagenomics",
    "quast": "Metagenomics",
    "spades": "Metagenomics",
    # Epigenomics
    "bismark": "Epigenomics",
    "macs2": "Epigenomics",
    "deeptools": "Epigenomics",
    "homer": "Epigenomics",
    "atacqc": "Epigenomics",
    "chromhmm": "Epigenomics",
    "epic2": "Epigenomics",
    "bedtools": "Epigenomics",
    # Single-cell
    "seurat": "Single-cell",
    "harmony": "Single-cell",
    "monocle3": "Single-cell",
    "scrublet": "Single-cell",
    "scenic": "Single-cell",
    "signac": "Single-cell",
    "doubletfinder": "Single-cell",
    "velocyto": "Single-cell",
    "cellranger": "Single-cell",
    # Proteomics
    "msfragger": "Proteomics",
    "flashlfq": "Proteomics",
    "percolator": "Proteomics",
    "philosopher": "Proteomics",
    "maxquant": "Proteomics",
    "moff": "Proteomics",
    "ionquant": "Proteomics",
    "skyline": "Proteomics",
    "fragpipe": "Proteomics",
    "tpp": "Proteomics",
    # Structural Biology
    "autodock": "Structural Biology",
    "pymol": "Structural Biology",
    "rosetta": "Structural Biology",
    "gromacs": "Structural Biology",
    "openmm": "Structural Biology",
    "modeller": "Structural Biology",
    "hmmer": "Structural Biology",
    "pdbfixer": "Structural Biology",
    "muscle": "Structural Biology",
    "iqtree": "Structural Biology",
    # ML / AI
    "pytorch": "ML / AI",
    "tensorflow": "ML / AI",
    "huggingface": "ML / AI",
    "sklearn": "ML / AI",
    "xgboost": "ML / AI",
    "rapids": "ML / AI",
    "esm2": "ML / AI",
    "alphafold2": "ML / AI",
    "cellpose": "ML / AI",
    "scanpy": "ML / AI",
    # More Bioinformatics
    "busco": "More Bioinformatics",
    "minimap2": "More Bioinformatics",
    "medaka": "More Bioinformatics",
    "nanostat": "More Bioinformatics",
    "flye": "More Bioinformatics",
    "repeatmasker": "More Bioinformatics",
    "trinity": "More Bioinformatics",
    "transdecoder": "More Bioinformatics",
    "interproscan": "More Bioinformatics",
    "orthofinder": "More Bioinformatics",
    "augustus": "More Bioinformatics",
    "fastp": "More Bioinformatics",
    # Population Genetics / GWAS
    "admixture": "Population Genetics",
    "eigensoft": "Population Genetics",
    "beagle": "Population Genetics",
    "shapeit": "Population Genetics",
    "gcta": "Population Genetics",
    "ldsc": "Population Genetics",
    "plink2": "Population Genetics",
    "regenie": "Population Genetics",
    "saige": "Population Genetics",
    "metal": "Population Genetics",
    "structure": "Population Genetics",
    "impute2": "Population Genetics",
}

# Tools that reuse another tool's SIF
REUSED_SIF: dict[str, str] = {
    # no separate Dockerfiles for these aliases; handled in build_all.sh comments
}

# Tools that require an external license
LICENSE_NEEDED = {"cellranger", "msfragger"}

# Internal-only dockerfiles to skip in the UI
SKIP_TOOLS = {"template", "base_with_runner", "obsolete"}

app = FastAPI(title="OmniBioAI Tool Images API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5184",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _tool_info(tool: str) -> dict:
    sif_path = SIF_DIR / f"{tool}_arm64.sif"
    sif_exists = sif_path.exists()
    sif_size_mb: float | None = None
    updated_at: str | None = None

    if sif_exists:
        stat = sif_path.stat()
        sif_size_mb = round(stat.st_size / (1024 * 1024), 1)
        updated_at = datetime.fromtimestamp(stat.st_mtime).isoformat()

    dockerfile = f"Dockerfile.{tool}"

    if tool in LICENSE_NEEDED:
        status = "license"
    elif not sif_exists:
        status = "missing"
    else:
        status = "built"

    return {
        "name": tool,
        "category": CATEGORY_MAP.get(tool, "Other"),
        "sif_path": str(sif_path) if sif_exists else None,
        "sif_filename": f"{tool}_arm64.sif",
        "sif_exists": sif_exists,
        "sif_size_mb": sif_size_mb,
        "status": status,
        "dockerfile": dockerfile,
        "updated_at": updated_at,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/tools")
def list_tools():
    tools = []
    for df in sorted(DOCKERFILES_DIR.glob("Dockerfile.*")):
        tool = df.name.removeprefix("Dockerfile.")
        if tool in SKIP_TOOLS:
            continue
        tools.append(_tool_info(tool))
    return tools


@app.get("/v1/tools/{tool}/dockerfile", response_class=PlainTextResponse)
def get_dockerfile(tool: str):
    df = DOCKERFILES_DIR / f"Dockerfile.{tool}"
    if not df.exists():
        raise HTTPException(status_code=404, detail="Dockerfile not found")
    return df.read_text()


@app.get("/v1/tools/{tool}/log", response_class=PlainTextResponse)
def get_build_log(tool: str):
    log = BUILD_LOGS_DIR / f"{tool}.log"
    if not log.exists():
        raise HTTPException(status_code=404, detail="Build log not found")
    return log.read_text()


async def _stream_build(cmd: list[str]) -> AsyncGenerator[dict, None]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(BASE),
    )
    assert proc.stdout is not None
    async for line in proc.stdout:
        yield {"data": line.decode(errors="replace").rstrip()}
    await proc.wait()
    rc = proc.returncode
    yield {"data": f"\n[exit code: {rc}]", "event": "done" if rc == 0 else "error"}


# NOT WIRED UP (issue #13, closed as won't-fix): build_all.sh isn't
# present in this image (Dockerfile only COPYs api/), and even if it were,
# this container has no docker CLI, no /var/run/docker.sock, and no
# singularity/apptainer binary -- build_all.sh needs all three. The real
# ARM64 SIF build path is the host-side
# omnibioai-tool-images/build_missing_sifs.sh script (see project memory
# project_sif_build.md), run directly on the host against its own Docker
# + Singularity install, not through this HTTP endpoint. These two routes
# are left as-is (they'll fail with exit 127) rather than silently
# reworked into something that looks functional but isn't the real build
# mechanism.
@app.post("/v1/build/{tool}")
def build_tool(tool: str):
    df = DOCKERFILES_DIR / f"Dockerfile.{tool}"
    if not df.exists():
        raise HTTPException(status_code=404, detail="Dockerfile not found")
    cmd = ["bash", "build_all.sh", tool]
    return EventSourceResponse(_stream_build(cmd))


@app.post("/v1/build-all")
def build_all():
    cmd = ["bash", "build_all.sh", "--parallel", "4"]
    return EventSourceResponse(_stream_build(cmd))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8097)
