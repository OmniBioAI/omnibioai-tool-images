# OmniBioAI — scenic
# Purpose: Build the OmniBioAI container image for scenic.
# Author: Manish Kumar <manish@omnibioai.org>
# Base image
FROM ubuntu:24.04
# Runtime environment
ENV DEBIAN_FRONTEND=noninteractive
# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip && rm -rf /var/lib/apt/lists/*
# Python dependencies
RUN pip3 install pyscenic scanpy anndata loompy --break-system-packages --no-cache-dir
# Runtime dependencies
RUN pip3 install boto3 azure-storage-blob azure-identity --break-system-packages --no-cache-dir
COPY omnibioai-tool-runtime/tools /app/tools
COPY omnibioai-tool-runtime/omni_tool_runtime /app/omni_tool_runtime
COPY omnibioai-tool-runtime/pyproject.toml /app/pyproject.toml
WORKDIR /app
RUN pip3 install . --break-system-packages
# Entrypoint and default command
CMD ["python3", "-m", "tools.generic_sif_runner.run"]
