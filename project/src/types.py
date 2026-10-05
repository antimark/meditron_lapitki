from dataclasses import dataclass, asdict, field
from typing import Any
@dataclass
class Evidence:
 text:str=""; start:int=-1; end:int=-1; sentence:str=""; sentence_start:int=-1; sentence_end:int=-1
 def dict(self): return asdict(self)
@dataclass
class Candidate:
 field:str; value:str; source:str; confidence:float; evidence:Evidence=field(default_factory=Evidence); raw_value:str=""; meta:dict[str,Any]=field(default_factory=dict)
 def dict(self):
  d=asdict(self); d['evidence']=self.evidence.dict(); return d
