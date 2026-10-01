#!/usr/bin/env python3
"""Create validated PASS provenance. Run with python -m scripts.record_pilot_provenance."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re

from scripts.pilot_integrity import validate_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = json.loads(args.evidence.read_text())
    validate_evidence(record)
    if not args.sif.is_file() or args.sif.stat().st_size == 0:
        raise ValueError("SIF missing or empty")
    actual = hashlib.sha256(args.sif.read_bytes()).hexdigest()
    sif_gate = record["sif_sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", sif_gate) or actual != sif_gate:
        raise ValueError("SIF SHA256 missing, malformed, or mismatched")
    record.update({
        "sif_sha256": actual,
        "sif_path": args.sif.name,
        "verification_timestamp": datetime.now(timezone.utc).isoformat(),
        "verification_result": "PASS",
        "sbom_status": "SBOM_OPTIONAL_NOT_GENERATED",
    })
    validate_evidence(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
