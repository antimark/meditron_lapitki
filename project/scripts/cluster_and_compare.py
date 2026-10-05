#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.batch_orchestrator import BatchOrchestrator
from src.config import load_config
from src.pipeline import Pipeline

p = argparse.ArgumentParser(description="Frequency analysis + HDBSCAN batch clustering without a predefined cluster count.")
p.add_argument("documents")
p.add_argument("--output", default="./cluster_analysis")
p.add_argument("--config", default="./config/default.toml")
a = p.parse_args()

cfg = load_config(a.config)
pipe = Pipeline(config=cfg)
orch = BatchOrchestrator(pipe, cfg)
out = Path(a.output)
out.mkdir(parents=True, exist_ok=True)
files = sorted(x for x in Path(a.documents).iterdir() if x.is_file() and x.suffix.lower() in {".md", ".txt"})
if not files:
    raise SystemExit("No .md/.txt files found")
prepared = orch.prepare([{"document_id": f.name, "text": f.read_text(encoding="utf-8-sig")} for f in files])
analysis = prepared.get("similarity", {})
(out / "batch_similarity.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")

try:
    import pandas as pd
    rows = []
    for name, meta in analysis.get("documents", {}).items():
        rows.append({"file": name, "cluster": meta.get("cluster"), "cluster_probability": meta.get("cluster_probability")})
    pd.DataFrame(rows).to_csv(out / "clusters.csv", index=False)
    pd.DataFrame(analysis.get("frequency", {}).get("top_document_frequency", [])).to_csv(out / "document_frequency.csv", index=False)
    top_rows = []
    for cluster, meta in analysis.get("clusters", {}).items():
        for rank, item in enumerate(meta.get("top_terms", []), 1):
            top_rows.append({"cluster": cluster, "rank": rank, **item})
    pd.DataFrame(top_rows).to_csv(out / "cluster_top_terms.csv", index=False)
except Exception:
    pass
print(out)
