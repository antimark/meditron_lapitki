from copy import deepcopy

from src.arbitration import arbitrate
from src.batch_orchestrator import BatchOrchestrator
from src.config import ROOT, load_config
from src.pipeline import Pipeline
from src.text_utils import evidence_from_span
from src.types import Candidate


def test_models_can_be_fully_disabled_by_environment(monkeypatch):
    cfg = load_config(ROOT / "config" / "default.toml")
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "0")
    monkeypatch.setenv("LLM_API_ENABLED", "0")
    pipe = Pipeline(config=cfg)
    status = pipe.provider_status()
    assert status["local_configured"] is False
    assert status["api_configured"] is False
    assert status["local_loaded"] is False
    assert status["api_loaded"] is False


def test_probe_compares_rules_api_then_calls_local_for_semantic_conflict():
    cfg = load_config(ROOT / "config" / "regex_only.toml")
    cfg = deepcopy(cfg)
    cfg.setdefault("pipeline", {}).update({"use_api": True, "use_local": True, "local_mode": "uncertain"})
    cfg.setdefault("api", {}).update({"enabled": True, "mode": "all"})
    cfg.setdefault("models", {}).update({"local_enabled": True, "local_backend": "disabled"})
    pipe = Pipeline(config=cfg)

    text = "ПЕРВИЧНЫЙ СТАТУС. Пульс 70 в минуту. Позже пульс 80 в минуту."
    called = {"api": None, "local": None}

    class FakeApi:
        def extract(self, text, sections, fields):
            called["api"] = list(fields)
            out = {f: [] for f in fields}
            st = text.index("пульс 80")
            out["bpm"] = [Candidate("bpm", "80", "api", 0.85, evidence_from_span(text, st, st + len("пульс 80")))]
            return out

    class FakeLocal:
        def extract(self, text, sections, fields):
            called["local"] = list(fields)
            out = {f: [] for f in fields}
            st = text.index("Пульс 70")
            if "bpm" in out:
                out["bpm"] = [Candidate("bpm", "70", "local", 0.82, evidence_from_span(text, st, st + len("Пульс 70")))]
            return out

    pipe._get_api = lambda: FakeApi()
    pipe._get_local = lambda: FakeLocal()
    pack = pipe.run(text, "probe.md", routing_policy={
        "strategy": "probe_compare",
        "api_mode": "all",
        "local_mode": "disagreement",
        "fallback_local_if_api_unavailable": True,
    })

    assert called["api"] is not None and len(called["api"]) == 50
    assert called["local"] == ["bpm"]
    assert pack["score"]["routing"]["comparison_events"]["bpm"]["event"] == "semantic_conflict"
    assert pack["score"]["routing"]["disagreement_fields"] == ["bpm"]


def test_probe_treats_regex_missing_api_evidenced_as_coverage_gap(monkeypatch):
    cfg = load_config(ROOT / "config" / "regex_only.toml")
    cfg = deepcopy(cfg)
    cfg.setdefault("pipeline", {}).update({"use_api": True, "use_local": True, "local_mode": "uncertain"})
    cfg.setdefault("api", {}).update({"enabled": True, "mode": "all"})
    cfg.setdefault("models", {}).update({"local_enabled": True, "local_backend": "disabled"})
    pipe = Pipeline(config=cfg)
    text = "Новый шаблон: ЧСС 81 уд/мин."
    monkeypatch.setattr(pipe, "_deterministic_sources", lambda text, sections: ({"regex": {}}, []))
    called = {"local": []}

    class FakeApi:
        def extract(self, text, sections, fields):
            out = {f: [] for f in fields}
            st = text.index("81")
            out["bpm"] = [Candidate("bpm", "81", "api", 0.9, evidence_from_span(text, st, st + 2))]
            return out

    class FakeLocal:
        def extract(self, text, sections, fields):
            called["local"] = list(fields)
            return {f: [] for f in fields}

    pipe._get_api = lambda: FakeApi()
    pipe._get_local = lambda: FakeLocal()
    pack = pipe.run(text, "gap.md", routing_policy={
        "strategy": "probe_compare",
        "api_mode": "all",
        "local_mode": "disagreement",
    })
    assert pack["score"]["routing"]["comparison_events"]["bpm"]["event"] == "regex_coverage_gap"
    assert "bpm" in called["local"]


def test_peer_prior_is_only_a_tiebreaker_and_never_creates_a_candidate():
    text = "ЭКГ: синусовый ритм."
    no_candidates = {"regex": {}}
    flat, _, _ = arbitrate(
        no_candidates,
        text,
        peer_prior={"ecg_rythm": {"фибрилляция предсердий": 1.0}},
        peer_prior_weight=0.5,
        peer_value_fields={"ecg_rythm"},
    )
    assert flat["ecg_rythm"] == "не указано"


def test_orchestrator_probe_count_is_configurable():
    cfg = load_config(ROOT / "config" / "default.toml")
    cfg = deepcopy(cfg)
    cfg["orchestration"]["probe_files"] = 3
    pipe = Pipeline(config=cfg)
    orch = BatchOrchestrator(pipe, cfg)
    assert orch.routing_policy(0)["strategy"] == "probe_compare"
    assert orch.routing_policy(2)["strategy"] == "probe_compare"
    assert orch.routing_policy(3)["strategy"] == "normal_uncertainty"


def test_prepare_selects_unique_probes_and_puts_them_first():
    cfg = load_config(ROOT / "config" / "regex_only.toml")
    cfg = deepcopy(cfg)
    cfg["orchestration"] = {
        "enabled": True,
        "probe_files": 5,
        "sampling_semantic_weight": 0.65,
        "sampling_extraction_mask_weight": 0.35,
    }
    cfg["batch_similarity"] = {
        "enabled": True,
        "min_batch_documents": 5,
        "min_cluster_size": 2,
        "min_samples": 1,
        "max_features": 1000,
        "min_df": 1,
        "max_df": 1.0,
        "svd_components": 5,
        "auto_stopword_docfreq": 1.1,
    }
    pipe = Pipeline(config=cfg)
    orch = BatchOrchestrator(pipe, cfg)
    docs = []
    for i in range(10):
        if i < 4:
            body = f"Инфаркт с подъемом ST. ЭКГ синусовый ритм. ЧСС {70+i}."
        elif i < 7:
            body = f"Острый инфаркт без подъема ST. ЭКГ синусовый ритм. ЧСС {70+i}."
        else:
            body = f"Нестабильная стенокардия. ЭКГ фибрилляция предсердий. ЧСС {70+i}."
        docs.append({"document_id": f"d{i}.md", "text": body})
    prepared = orch.prepare(docs)
    probes = prepared["probe_document_ids"]
    assert len(probes) == 5
    assert len(set(probes)) == 5
    assert prepared["processing_order"][:5] == prepared["probe_indices"]


def test_probe_results_expand_routing_globally_and_per_cluster():
    cfg = load_config(ROOT / "config" / "default.toml")
    cfg = deepcopy(cfg)
    cfg["orchestration"].update({
        "probe_files": 5,
        "adaptive_min_probe_observations_global": 3,
        "adaptive_regex_gap_rate": 0.30,
        "adaptive_min_probe_observations_cluster": 1,
        "adaptive_cluster_regex_gap_rate": 0.50,
    })
    pipe = Pipeline(config=cfg)
    orch = BatchOrchestrator(pipe, cfg)
    prepared = {
        "probe_document_ids": [f"p{i}.md" for i in range(5)],
        "similarity": {"documents": {f"p{i}.md": {"cluster": 0 if i < 3 else 1} for i in range(5)} | {"target.md": {"cluster": 1}}},
        "batch_regex_shift": {"available": False, "flagged_fields": [], "fields": {}},
        "calibration": {"probe_results": {}, "ready": False, "completed": 0, "expected": 5, "adaptive_policy": {}},
    }
    for i in range(5):
        events = {
            "hf": {"event": "regex_coverage_gap" if i < 3 else "agree"},
            "echo_zone": {"event": "regex_coverage_gap" if i == 3 else "agree"},
        }
        pack = {"score": {"routing": {"api_provider_used": True, "local_provider_used": True, "comparison_events": events, "disagreement_fields": []}}}
        orch.record_probe_result(prepared, f"p{i}.md", pack)

    policy = prepared["calibration"]["adaptive_policy"]
    assert policy["ready"] is True
    assert policy["global_fields"]["hf"]["mode"] == "api_always"
    assert policy["cluster_fields"]["1"]["echo_zone"]["mode"] == "api_always"

    route = orch.routing_policy(99, document_id="target.md", prepared=prepared)
    assert route["strategy"] == "adaptive_uncertainty"
    assert "hf" in route["api_always_fields"]
    assert "echo_zone" in route["api_always_fields"]
    assert "hf" in route["local_on_api_disagreement_fields"]
