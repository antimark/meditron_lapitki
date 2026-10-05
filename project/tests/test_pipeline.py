from pathlib import Path
import json
from src.pipeline import Pipeline
from src.schema import ALL_FIELDS, flatten, NOT_SPEC
from src.io import write_outputs

ROOT=Path(__file__).resolve().parents[1]
PIPE=Pipeline(str(ROOT/'config'/'regex_only.toml'))

def test_sample_contract_and_context(tmp_path):
    text=(ROOT/'examples'/'train-0001.md').read_text(encoding='utf-8')
    pack=PIPE.run(text,'train-0001.md')
    flat=flatten(pack['result'])
    assert len(flat)==50 and set(flat)==set(ALL_FIELDS)
    assert flat['admission_date']=='01.01.2020'
    assert flat['diagnosis_icd']=='I21.0'
    assert flat['crea']=='99'
    for f,x in pack['context']['fields'].items():
        if x['evidence_start']>=0:
            assert text[x['evidence_start']:x['evidence_end']]==x['evidence_text']
    paths=write_outputs(pack,tmp_path,'train-0001')
    assert Path(paths['result']).name=='train-0001.json'
    assert Path(paths['score']).name=='train-0001_score.json'
    assert Path(paths['context']).name=='train-0001_context.json'

def test_blank_file_score_zero():
    pack=PIPE.run('', 'blank.md')
    assert pack['score']['quality_score']==0.0
    assert pack['score']['quality_label']=='bad'

def test_ecg_absence_policy():
    text='ЭКГ:\nСинусовый ритм, ЧСС 70 уд/мин.'
    flat=flatten(PIPE.run(text,'x.md')['result'])
    assert flat['ecg_avb']=='0'
    assert flat['ecg_elevation']=='0'
    text2='ЭКГ:\nСинусовый ритм. Элевации ST нет. АВ-блокада II степени.'
    flat2=flatten(PIPE.run(text2,'y.md')['result'])
    assert flat2['ecg_elevation']=='0'
    assert flat2['ecg_avb']=='1'

def test_cag_max_stenosis():
    text='КАГ:\nВыполнена коронарография. ПМЖВ: стеноз 50%, далее стеноз 95%. ПКА: стеноз 20%.'
    flat=flatten(PIPE.run(text,'cag.md')['result'])
    assert flat['ca_fact']=='Y'
    assert flat['ca_lad']=='2'
    assert flat['rca']=='0'

def test_echo_zone_requires_local_zone():
    a=flatten(PIPE.run('ЭхоКГ:\nДиффузная гипокинезия миокарда. ФВ 40%.','e1.md')['result'])
    b=flatten(PIPE.run('ЭхоКГ:\nГипокинез нижней стенки. ФВ 40%.','e2.md')['result'])
    assert a['echo_zone']==NOT_SPEC
    assert 'гипокинез' in b['echo_zone'].lower()


def test_case_first_acs_unstable_angina_is_na():
    flat=flatten(PIPE.run('Заключительный диагноз:\nИБС. Нестабильная стенокардия.','ua.md')['result'])
    assert flat['type_acs']=='NA'

def test_case_first_ecg_avb_degree_is_binary_presence():
    for degree in ('I','II','III'):
        flat=flatten(PIPE.run(f'ЭКГ:\nАВ-блокада {degree} степени.','avb.md')['result'])
        assert flat['ecg_avb']=='1'
