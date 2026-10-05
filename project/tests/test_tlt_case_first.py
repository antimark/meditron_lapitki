from pathlib import Path

from src.llm_common import make_batches
from src.pipeline import Pipeline
from src.schema import flatten
from src.sections import split_sections

ROOT = Path(__file__).resolve().parents[1]
PIPE = Pipeline(str(ROOT / "config" / "regex_only.toml"))


def _tlt(text: str):
    pack = PIPE.run(text, "tlt.md")
    return flatten(pack["result"])["tlt"], pack


def test_tlt_detected_outside_named_treatment_section():
    value, pack = _tlt(
        "Анамнез заболевания:\nДоставлен с ОКС. В догоспитальном этапе проведена ТЛТ с эффектом."
    )
    assert value == "1"
    assert pack["context"]["fields"]["tlt"]["source"] == "regex"
    assert "ТЛТ" in pack["context"]["fields"]["tlt"]["evidence_text"]


def test_tlt_detected_in_combined_treatment_outcome_heading():
    text = "Лечение и исход:\nПроведена тромболитическая терапия, далее пациент направлен на КАГ."
    sections = split_sections(text)
    assert sections.get("treatment")
    value, _ = _tlt(text)
    assert value == "1"


def test_tlt_detected_by_named_thrombolytic_administration():
    value, _ = _tlt("Течение госпитализации:\nВведена тенектеплаза, достигнута реперфузия.")
    assert value == "1"


def test_tlt_negative_and_pci_do_not_become_positive():
    value, _ = _tlt("Лечение:\nТЛТ не проводилась. Выполнено ЧКВ со стентированием ПМЖВ.")
    assert value == "0"
    value2, _ = _tlt("Лечение:\nВыполнено первичное ЧКВ, стентирование ПМЖВ, тромбоаспирация.")
    assert value2 == "0"


def test_tlt_episode_scope_is_isolated_in_llm_batches():
    text = (
        "Заключительный диагноз:\nОсновной диагноз I21.0.\n"
        "Прочее:\nПроведена ТЛТ на догоспитальном этапе."
    )
    sections = split_sections(text)
    batches = make_batches(text, sections, ["diagnosis_icd", "tlt"], grouped=True, max_chars=14000)
    by_fields = {tuple(fields): batch_text for fields, batch_text in batches}
    assert ("tlt",) in by_fields
    assert by_fields[("tlt",)] == text
    assert ("diagnosis_icd",) in by_fields
    assert "Проведена ТЛТ" not in by_fields[("diagnosis_icd",)]


def test_tlt_participates_in_deterministic_preview_for_batch_orchestration():
    preview = PIPE.deterministic_preview("Любой раздел:\nПроведена ТЛТ.")
    assert preview["flat"]["tlt"] == "1"
    assert preview["audit"]["tlt"]["score"] >= 0.9
