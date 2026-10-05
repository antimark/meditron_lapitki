"""Benchmark one or more fine-tuned/local model configs through the full pipeline against reviewed gold JSONs."""
import argparse, csv, json, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.schema import flatten, ALL_FIELDS

p=argparse.ArgumentParser(); p.add_argument('documents'); p.add_argument('gold'); p.add_argument('--runs', nargs='+', required=True, help='name=config.toml'); p.add_argument('--out', default='./model_benchmark.csv'); a=p.parse_args()
rows=[]
for item in a.runs:
    name, cfg = item.split('=',1)
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'process_folder.py'), a.documents, '--output', td, '--config', cfg, '--workers', '1'], check=True, stdout=subprocess.DEVNULL)
        correct=total=0
        per={f:[0,0] for f in ALL_FIELDS}
        for rp in Path(td,'result').glob('*.json'):
            gp=Path(a.gold,'result',rp.name)
            if not gp.exists(): gp=Path(a.gold,rp.name)
            if not gp.exists(): continue
            pr, gd = flatten(json.loads(rp.read_text(encoding='utf-8'))), flatten(json.loads(gp.read_text(encoding='utf-8')))
            for f in ALL_FIELDS:
                total+=1; per[f][1]+=1
                if pr[f]==gd[f]: correct+=1; per[f][0]+=1
        rows.append({'run':name,'field':'__ALL__','exact_match':correct/total if total else 0,'n':total})
        rows += [{'run':name,'field':f,'exact_match':c/n if n else 0,'n':n} for f,(c,n) in per.items()]
with open(a.out,'w',newline='',encoding='utf-8-sig') as fh:
    w=csv.DictWriter(fh,fieldnames=['run','field','exact_match','n']); w.writeheader(); w.writerows(rows)
print(a.out)
