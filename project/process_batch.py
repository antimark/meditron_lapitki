from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.batch_orchestrator import BatchOrchestrator
from src.io import write_outputs
from src.pipeline import Pipeline

p = argparse.ArgumentParser(description="Process a manifest batch with configurable adaptive probe/similarity routing.")
p.add_argument("manifest")
p.add_argument("--output", default="./out")
p.add_argument("--config", default=None)
p.add_argument("--workers", type=int, default=None)
p.add_argument("--no-batch-analysis", action="store_true")
a = p.parse_args()

pipe = Pipeline(a.config)
orch = BatchOrchestrator(pipe)
workers = a.workers or int(pipe.cfg.get("runtime", {}).get("folder_workers", 4))
jobs = []
for line in Path(a.manifest).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    try:
        obj = json.loads(line)
        if isinstance(obj, str):
            obj = {"path": obj}
    except json.JSONDecodeError:
        obj = {"path": line.strip()}
    path = Path(obj["path"])
    obj["_text"] = path.read_text(encoding="utf-8-sig")
    obj["_id"] = obj.get("id", path.name)
    obj["_stem"] = path.stem
    jobs.append(obj)

prepared = None if a.no_batch_analysis else orch.prepare([{"document_id": x["_id"], "text": x["_text"]} for x in jobs])
probe_indices = list((prepared or {}).get("probe_indices", []))
packs = {}
errors = []

def one(i):
    job = jobs[i]
    pack = pipe.run(job["_text"], job["_id"]) if prepared is None else orch.process(job["_text"], job["_id"], i, prepared)
    return i, pack

# Selected probes are intentionally processed first and sequentially. Their
# comparison statistics define routing for the rest of the batch.
for i in probe_indices:
    try:
        _, pack = one(i)
        packs[jobs[i]["_id"]] = pack
        orch.record_probe_result(prepared, jobs[i]["_id"], pack)
    except Exception as exc:
        errors.append({"id": jobs[i]["_id"], "status": "error", "error": repr(exc)})

remaining = [i for i in range(len(jobs)) if i not in set(probe_indices)]
with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
    fm = {ex.submit(one, i): i for i in remaining}
    for fut in as_completed(fm):
        i = fm[fut]
        try:
            _, pack = fut.result(); packs[jobs[i]["_id"]] = pack
        except Exception as exc:
            errors.append({"id": jobs[i]["_id"], "status": "error", "error": repr(exc)})

if prepared is not None:
    orch.finalize_packs(packs, prepared)

for job in jobs:
    pack = packs.get(job["_id"])
    if not pack:
        continue
    out = job.get("output", a.output)
    paths = write_outputs(pack, out, job["_stem"])
    print(json.dumps({"id": job["_id"], "status": "ok", **paths, "quality_score": pack["score"]["quality_score"], "quality_label": pack["score"]["quality_label"]}, ensure_ascii=False))
for err in errors:
    print(json.dumps(err, ensure_ascii=False))
