import argparse, csv, json, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.schema import flatten, ALL_FIELDS, NOT_SPEC

p=argparse.ArgumentParser();p.add_argument('predictions',help='output root containing result/');p.add_argument('gold',help='gold root containing result/ or json files');p.add_argument('--out',default='./evaluation');a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
per=defaultdict(lambda:{'correct':0,'n':0,'tp':0,'fp':0,'fn':0}); docs=[]
for rp in sorted((Path(a.predictions)/'result').glob('*.json')):
 gp=Path(a.gold)/'result'/rp.name
 if not gp.exists():gp=Path(a.gold)/rp.name
 if not gp.exists():continue
 pr,gd=flatten(json.loads(rp.read_text(encoding='utf-8'))),flatten(json.loads(gp.read_text(encoding='utf-8')))
 dc=0
 for f in ALL_FIELDS:
  d=per[f];d['n']+=1
  if pr[f]==gd[f]:d['correct']+=1;dc+=1
  pp,gg=pr[f]!=NOT_SPEC,gd[f]!=NOT_SPEC
  if pp and gg:d['tp']+=1
  elif pp and not gg:d['fp']+=1
  elif not pp and gg:d['fn']+=1
 docs.append({'file':rp.name,'exact_match':dc/len(ALL_FIELDS)})
rows=[]
for f in ALL_FIELDS:
 d=per[f];prec=d['tp']/(d['tp']+d['fp']) if d['tp']+d['fp'] else 1.0;rec=d['tp']/(d['tp']+d['fn']) if d['tp']+d['fn'] else 1.0;f1=2*prec*rec/(prec+rec) if prec+rec else 0
 rows.append({'field':f,'exact_match':d['correct']/d['n'] if d['n'] else 0,'n':d['n'],'presence_precision':prec,'presence_recall':rec,'presence_f1':f1})
for name,data in [('field_metrics.csv',rows),('file_metrics.csv',docs)]:
 with open(out/name,'w',newline='',encoding='utf-8-sig') as fh:
  w=csv.DictWriter(fh,fieldnames=data[0].keys() if data else ['field']);w.writeheader();w.writerows(data)
print(out)
