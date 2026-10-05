from __future__ import annotations
from pathlib import Path
import re
try:
 from rapidfuzz import process, fuzz
except Exception:
 process=fuzz=None
class TypoCorrector:
 def __init__(self, lexicon_path, score_cutoff=88):
  self.words=[x.strip().lower() for x in Path(lexicon_path).read_text(encoding='utf-8').splitlines() if x.strip()]
  self.cutoff=score_cutoff
 def correct(self,text:str):
  """Return corrected shadow text, correction log and shadow->original char map.
  Original text is never modified. The map lets candidates found in the shadow text
  be projected back to exact UI offsets in the original document.
  """
  if process is None: return text,[],list(range(len(text)+1))
  corrections=[]; parts=[]; idxmap=[]; pos=0
  for m in re.finditer(r'[A-Za-zА-Яа-яЁё-]{5,}',text):
   prefix=text[pos:m.start()]; parts.append(prefix); idxmap.extend(range(pos,m.start()))
   tok=m.group(0); low=tok.lower().replace('ё','е'); hit=process.extractOne(low,self.words,scorer=fuzz.ratio,score_cutoff=self.cutoff); repl=tok
   if hit and hit[0]!=low and abs(len(hit[0])-len(low))<=1 and low[:2]==hit[0][:2] and low[-2:]==hit[0][-2:]:
    # Conservative rule: preserve grammatical endings. This targets internal typos/duplications
    # and avoids turning valid inflected forms into dictionary lemmas.
    repl=hit[0]; corrections.append({'original':tok,'corrected':repl,'start':m.start(),'end':m.end(),'score':hit[1]})
   parts.append(repl)
   if len(repl):
    # monotonic proportional map across the original token span
    L=max(1,len(tok))
    idxmap.extend([m.start()+min(L-1,int(i*L/max(1,len(repl)))) for i in range(len(repl))])
   pos=m.end()
  parts.append(text[pos:]);idxmap.extend(range(pos,len(text)));idxmap.append(len(text))
  return ''.join(parts),corrections,idxmap
