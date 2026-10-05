"""Build SFT JSONL from documents + reviewed result/context files.

Default mode mirrors runtime: one example per clinical group using the same field instructions
and section-focused text. Labels should be manually reviewed or organizer-provided.
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.schema import flatten, FIELD_GROUPS, NOT_SPEC
from src.llm_common import build_prompt, make_batches
from src.sections import split_sections

p = argparse.ArgumentParser()
p.add_argument("documents")
p.add_argument("labels")
p.add_argument("--out", default="finetune.jsonl")
p.add_argument("--mode", choices=["group", "full"], default="group")
p.add_argument("--require-context", action="store_true")
a = p.parse_args()

doc_dir, lab_root = Path(a.documents), Path(a.labels)
rows, skipped = [], []
for doc in sorted(x for x in doc_dir.iterdir() if x.suffix.lower() in {".md", ".txt"}):
    result_path = lab_root / "result" / f"{doc.stem}.json"
    if not result_path.exists(): result_path = lab_root / f"{doc.stem}.json"
    if not result_path.exists():
        skipped.append({"file": doc.name, "reason": "missing_result"}); continue
    context_path = lab_root / "context" / f"{doc.stem}_context.json"
    context = {}
    if context_path.exists():
        cobj = json.loads(context_path.read_text(encoding="utf-8")); context = cobj.get("fields", cobj)
    elif a.require_context:
        skipped.append({"file": doc.name, "reason": "missing_context"}); continue
    flat = flatten(json.loads(result_path.read_text(encoding="utf-8")))
    text = doc.read_text(encoding="utf-8")
    sections = split_sections(text)
    batches = make_batches(text, sections, [f for fs in FIELD_GROUPS.values() for f in fs], grouped=(a.mode=="group"), max_chars=14000)
    for fields, batch_text in batches:
        system, user = build_prompt(batch_text, fields)
        target = {}
        for f in fields:
            v = flat[f]; q = ""
            if f in context: q = str(context[f].get("evidence_text", "") or "")
            target[f] = {"value": v, "evidence": q if v != NOT_SPEC else ""}
        rows.append({"messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
        ], "document_id": doc.name, "fields": fields})
Path(a.out).write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
Path(str(a.out)+".skipped.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"rows":len(rows),"documents":len({r['document_id'] for r in rows}),"skipped":len(skipped),"out":a.out},ensure_ascii=False))
