from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from src.arbitration import label_file_score
from src.batch_orchestrator import BatchOrchestrator
from src.config import ROOT
from src.pipeline import Pipeline
from src.schema import flatten

_PIPELINE = None
_PIPELINE_LOCK = threading.Lock()
_ORCHESTRATOR = None
_ORCHESTRATOR_LOCK = threading.Lock()


def _pipeline_config_path() -> str:
    return os.getenv("CARDIO_PIPELINE_CONFIG", str(ROOT / "config" / "default.toml"))


def get_pipeline() -> Pipeline:
    global _PIPELINE
    if _PIPELINE is None:
        with _PIPELINE_LOCK:
            if _PIPELINE is None:
                _PIPELINE = Pipeline(_pipeline_config_path())
    return _PIPELINE


def get_batch_orchestrator() -> BatchOrchestrator:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        with _ORCHESTRATOR_LOCK:
            if _ORCHESTRATOR is None:
                _ORCHESTRATOR = BatchOrchestrator(get_pipeline())
    return _ORCHESTRATOR


def _write_pack(pack: dict, data_dir: str | Path, stem: str) -> None:
    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.json").write_text(
        json.dumps(pack["result"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / f"{stem}_context.json").write_text(
        json.dumps(pack["context"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / f"{stem}_score.json").write_text(
        json.dumps(pack["score"], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def process_md(
    md_file: str | Path,
    data_dir: str | Path,
    routing_policy: dict[str, Any] | None = None,
    peer_prior: dict | None = None,
):
    """Run the extraction pipeline and write UI-compatible artifacts."""
    md_path = Path(md_file)
    text = md_path.read_text(encoding="utf-8")
    pack = get_pipeline().run(
        text,
        md_path.name,
        routing_policy=routing_policy,
        peer_prior=peer_prior,
    )
    _write_pack(pack, data_dir, md_path.stem)
    return pack


def prepare_batch(md_paths: list[str | Path]) -> dict[str, Any]:
    """Cheap batch pre-pass: rules-only previews + optional similarity analysis."""
    documents = []
    for path in md_paths:
        p = Path(path)
        documents.append({
            "document_id": p.name,
            "text": p.read_text(encoding="utf-8"),
        })
    return get_batch_orchestrator().prepare(documents)


def process_batch_item(
    md_file: str | Path,
    data_dir: str | Path,
    index: int,
    prepared: dict[str, Any] | None,
):
    md_path = Path(md_file)
    orchestrator = get_batch_orchestrator()
    routing = orchestrator.routing_policy(index, document_id=md_path.name, prepared=prepared)
    peer_prior = None
    if prepared:
        peer_prior = prepared.get("similarity", {}).get("peer_priors", {}).get(md_path.name)
    return process_md(md_path, data_dir, routing_policy=routing, peer_prior=peer_prior)


def finalize_batch_scores(
    md_paths: list[str | Path],
    data_dir: str | Path,
    prepared: dict[str, Any] | None,
) -> dict[str, Any]:
    """Recalculate only the bounded batch-level confidence adjustment.

    Extraction is not rerun and peer statistics never fill an absent value.
    """
    if not prepared:
        return {"enabled": False, "updated": 0}
    analysis = prepared.get("similarity", {})
    if not analysis.get("enabled"):
        return {"enabled": False, "updated": 0, "reason": analysis.get("reason")}

    data_dir = Path(data_dir)
    flats: dict[str, dict[str, str]] = {}
    scores: dict[str, dict] = {}
    path_by_id: dict[str, Path] = {}
    for item in md_paths:
        p = Path(item)
        result_path = data_dir / f"{p.stem}.json"
        score_path = data_dir / f"{p.stem}_score.json"
        if not result_path.exists() or not score_path.exists():
            continue
        try:
            nested = json.loads(result_path.read_text(encoding="utf-8"))
            score = json.loads(score_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        flats[p.name] = flatten(nested)
        scores[p.name] = score
        path_by_id[p.name] = score_path

    analyzer = get_batch_orchestrator().similarity
    updated = 0
    details = {}
    for doc_id, flat in flats.items():
        score = scores[doc_id]
        base = float(score.get("base_quality_score", score.get("quality_score", 0.0)))
        consistency = analyzer.score_consistency(doc_id, flats, analysis)
        adjusted, batch_meta = analyzer.adjust_score(base, consistency)
        score["base_quality_score"] = round(base, 4)
        score["quality_score"] = adjusted
        score["batch_similarity"] = batch_meta
        score["quality_label"] = label_file_score(
            adjusted,
            score.get("field_quality", {}),
            flat,
            score.get("cross_field_issues", []),
        )
        path_by_id[doc_id].write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
        updated += 1
        details[doc_id] = batch_meta

    return {"enabled": True, "updated": updated, "documents": details}
