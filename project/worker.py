"""Long-lived stdin/stdout JSONL worker.

Start N worker processes behind the UI/job queue. The model is loaded once per worker.
Input examples:
  {"id":"job-1","path":"/data/train_0001.md","output":"/data/out"}
  {"id":"job-2","text":"...","stem":"inline_1","output":"/data/out"}
Output is one JSON line with exact result/score/context paths.
"""
import argparse, json, sys
from pathlib import Path
from src.pipeline import Pipeline
from src.io import write_outputs

ap = argparse.ArgumentParser()
ap.add_argument("--config", default=None)
ap.add_argument("--output", default="./out")
args = ap.parse_args()
pipe = Pipeline(args.config)

for line in sys.stdin:
    try:
        job = json.loads(line)
        if "path" in job:
            path = Path(job["path"])
            text = path.read_text(encoding="utf-8")
            stem = job.get("stem", path.stem)
            doc_id = job.get("id", path.name)
        else:
            text = job["text"]
            stem = job.get("stem", job.get("id", "document"))
            doc_id = job.get("id", stem)
        out = job.get("output", args.output)
        pack = pipe.run(text, doc_id)
        paths = write_outputs(pack, out, stem)
        print(json.dumps({"id": job.get("id"), "status": "ok", **paths, "quality_score": pack["score"]["quality_score"], "quality_label": pack["score"]["quality_label"]}, ensure_ascii=False), flush=True)
    except Exception as e:
        print(json.dumps({"status": "error", "error": repr(e)}, ensure_ascii=False), flush=True)
