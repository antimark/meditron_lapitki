#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.anonymizer import MedicalTextAnonymizer
from src.batch_orchestrator import BatchOrchestrator
from src.io import write_outputs
from src.pipeline import Pipeline


def parse_args():
    p = argparse.ArgumentParser(description="Process a folder with optional batch probe calibration and similarity analysis.")
    p.add_argument("folder", help="Folder containing .md/.txt files")
    p.add_argument("--output", default="./batch_run", help="Output root")
    p.add_argument("--config", default="./config/default.toml", help="Pipeline TOML")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--no-recursive", action="store_true")
    p.add_argument("--anonymize", action="store_true")
    p.add_argument("--anonymizer-mode", choices=["clinical", "strict"], default="clinical")
    p.add_argument("--no-anonymizer-ner", action="store_true")
    p.add_argument("--no-batch-analysis", action="store_true", help="Disable probe calibration and batch similarity for this run")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    source = Path(a.folder).resolve()
    output = Path(a.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    pattern = source.glob("*") if a.no_recursive else source.rglob("*")
    files = sorted(x for x in pattern if x.is_file() and x.suffix.lower() in {".md", ".txt"})
    if not files:
        raise SystemExit("No .md/.txt files found")

    config_path = str((ROOT / a.config).resolve()) if not Path(a.config).is_absolute() else a.config
    pipe = Pipeline(config_path)
    orchestrator = BatchOrchestrator(pipe)
    workers = a.workers or int(pipe.cfg.get("runtime", {}).get("folder_workers", 4))

    anonymizer = None
    if a.anonymize:
        anonymizer = MedicalTextAnonymizer(a.anonymizer_mode, not a.no_anonymizer_ner)
        (output / "anonymized_inputs").mkdir(parents=True, exist_ok=True)
        (output / "anonymization_reports").mkdir(parents=True, exist_ok=True)

    docs = []
    for path in files:
        text = path.read_text(encoding="utf-8-sig")
        stem, doc_id = path.stem, path.name
        anon_safe = None
        if anonymizer is not None:
            text, report = anonymizer.anonymize(text)
            stem = f"{stem}_anon"
            doc_id = f"{stem}{path.suffix}"
            (output / "anonymized_inputs" / doc_id).write_text(text, encoding="utf-8")
            (output / "anonymization_reports" / f"{stem}_report.json").write_text(
                json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            anon_safe = report.safe
        docs.append({"path": path, "text": text, "stem": stem, "document_id": doc_id, "anonymization_safe": anon_safe})

    prepared = None
    if not a.no_batch_analysis:
        prepared = orchestrator.prepare([{"document_id": d["document_id"], "text": d["text"]} for d in docs])
        (output / "batch_analysis.json").write_text(json.dumps(prepared.get("similarity", {}), ensure_ascii=False, indent=2), encoding="utf-8")

    packs = {}
    errors = []
    probe_indices = list((prepared or {}).get("probe_indices", [])) if not a.no_batch_analysis else []

    def process_index(index: int):
        d = docs[index]
        if prepared is None:
            pack = pipe.run(d["text"], d["document_id"])
        else:
            pack = orchestrator.process(d["text"], d["document_id"], index, prepared)
        return index, pack

    # Representative/diverse probes stay ordered because their observed
    # disagreements build the adaptive policy used by the remaining documents.
    for index in probe_indices:
        try:
            _, pack = process_index(index)
            packs[docs[index]["document_id"]] = pack
            orchestrator.record_probe_result(prepared, docs[index]["document_id"], pack)
        except Exception as exc:
            errors.append({"input": str(docs[index]["path"]), "error": repr(exc)})

    remaining = [i for i in range(len(docs)) if i not in set(probe_indices)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        future_map = {ex.submit(process_index, i): i for i in remaining}
        for fut in as_completed(future_map):
            i = future_map[fut]
            try:
                _, pack = fut.result()
                packs[docs[i]["document_id"]] = pack
            except Exception as exc:
                errors.append({"input": str(docs[i]["path"]), "error": repr(exc)})

    if prepared is not None:
        orchestrator.finalize_packs(packs, prepared)

    rows = []
    for d in docs:
        pack = packs.get(d["document_id"])
        if pack is None:
            continue
        paths = write_outputs(pack, output, d["stem"])
        row = {
            "status": "ok", "input": str(d["path"]), "stem": d["stem"],
            "quality_score": pack["score"]["quality_score"], "quality_label": pack["score"]["quality_label"],
            "anonymization_safe": d["anonymization_safe"], "paths": paths,
        }
        rows.append(row)
        print(f"OK\t{d['path'].name}\t{row['quality_score']:.4f}\t{row['quality_label']}")

    rows.extend({"status": "error", **x} for x in errors)
    summary = {
        "input_dir": str(source), "output_dir": str(output), "config": a.config,
        "processed": len(rows), "ok": sum(r["status"] == "ok" for r in rows),
        "errors": sum(r["status"] == "error" for r in rows), "batch_analysis": prepared is not None,
        "items": rows,
    }
    (output / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if summary["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
