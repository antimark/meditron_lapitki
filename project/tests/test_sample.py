from pathlib import Path
from src.regex_extractor import extract_regex
from src.sections import split_sections
def test_sample_regex():
 t=Path('examples/train-0001.md').read_text(encoding='utf-8'); r=extract_regex(t,split_sections(t))
 assert r['admission_date'][0].value=='01.01.2020'
 assert r['ca_fact'][0].value=='R'
 assert r['crea'][0].value=='99'
