# OmniBioAI — metal
# Purpose: Build the OmniBioAI container image for metal.
# Author: Manish Kumar <manish@omnibioai.org>
# Base image
FROM ubuntu:24.04
# System dependencies
RUN apt-get update && apt-get install -y python3-pip \
    g++ \
    make \
    zlib1g-dev \
    git \
    && git clone https://github.com/statgen/METAL.git /tmp/metal \
    && cd /tmp/metal && mkdir build && cd build \
    && apt-get install -y cmake \
    && cmake .. && make && make install \
    && ln -s /usr/local/bin/metal /usr/local/bin/metal 2>/dev/null || true
# ── generic_sif_runner (handles S3/Azure download + results upload) ──
RUN pip3 install boto3 azure-storage-blob azure-identity --break-system-packages

COPY omnibioai-tool-runtime/tools /app/tools
COPY omnibioai-tool-runtime/omni_tool_runtime /app/omni_tool_runtime
COPY omnibioai-tool-runtime/pyproject.toml /app/pyproject.toml
WORKDIR /app
RUN pip3 install . --break-system-packages

# Entrypoint and default command
CMD ["python3", "-m", "tools.generic_sif_runner.run"]
