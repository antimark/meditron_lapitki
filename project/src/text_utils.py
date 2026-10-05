from __future__ import annotations
import re
from .types import Evidence

def sentence_spans(text:str):
 # Preserve exact original positions; newline can delimit a medical statement too.
 spans=[]
 start=0
 for m in re.finditer(r'(?<=[.!?])\s+|\n+', text):
  end=m.start(); s=text[start:end].strip()
  if s:
   l=start+len(text[start:end])-len(text[start:end].lstrip()); r=end-len(text[start:end])+len(text[start:end].rstrip())
   spans.append((l,r,text[l:r]))
  start=m.end()
 if start<len(text):
  s=text[start:].strip()
  if s:
   l=start+len(text[start:])-len(text[start:].lstrip()); r=len(text)-len(text[start:])+len(text[start:].rstrip())
   spans.append((l,r,text[l:r]))
 return spans

def evidence_from_span(text,start,end):
 for s0,s1,s in sentence_spans(text):
  if s0<=start<s1 or (start<=s0 and end>=s0):
   return Evidence(text=text[start:end],start=start,end=end,sentence=s,sentence_start=s0,sentence_end=s1)
 return Evidence(text=text[start:end],start=start,end=end,sentence=text[start:end],sentence_start=start,sentence_end=end)

def normalize_for_match(s:str):
 s=s.lower().replace('ё','е').replace(',','.')
 s=re.sub(r'\s+',' ',s).strip()
 return s

def value_supported(value:str, ev:Evidence, original:str)->bool:
 if not value or value=='не указано': return True
 if ev.start<0 or ev.end<0 or ev.end>len(original): return False
 if original[ev.start:ev.end]!=ev.text: return False
 v=normalize_for_match(value); e=normalize_for_match((ev.text or '') + ' ' + (ev.sentence or ''))
 # coded values are transformations; evidence existence is the support condition.
 if value in {'0','1','2','3','4','A','I','L','N','Y','R','STEMI','NSTEMI','NA'}: return bool(ev.text.strip())
 if v in e: return True
 # numeric normalization / dosage values
 nums=re.findall(r'\d+(?:\.\d+)?',v)
 return bool(nums) and all(n in e for n in nums)
