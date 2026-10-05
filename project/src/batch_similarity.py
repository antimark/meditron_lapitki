"""Unsupervised batch-similarity analysis used for routing and QA.

Text clustering and peer statistics help select calibration probes and detect
outliers. Cohort information is deliberately prevented from filling missing
patient values; it can only act as a weak tie-breaker or bounded score penalty.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
import re
from typing import Any

from .config import ROOT
from .schema import ALL_FIELDS, BINARY_FIELDS, NOT_SPEC

_DATE_RX = re.compile(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b")
_NUMBER_RX = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_TOKEN_RX = re.compile(r"[а-яa-z]{2,}(?:-[а-яa-z]{2,})*", re.I)

# Presence for technical/coded fields is often guaranteed by the contract (0/N),
# therefore those fields would artificially inflate peer consistency and mask distance.
_PRESENCE_FIELDS = [f for f in ALL_FIELDS if f not in BINARY_FIELDS and f != "ca_fact"]

# High-frequency tokens that must remain available to the vectorizer because
# in clinical text they change meaning (especially negation). They are excluded
# from both static and automatic stopword sets.
_PROTECTED_SEMANTIC_TOKENS = {
    "не", "без", "нет", "отрицает", "отрицательный", "отрицательная",
    "положительный", "положительная", "подъем", "подъема", "подъём", "подъёма",
    "элевация", "инфаркт", "стенокардия", "окклюзия",
}

_DEFAULT_CLINICAL_WEIGHTS = {
    "diagnosis_icd": 3.0,
    "type_acs": 2.5,
    "mi_localisation": 2.0,
    "killip": 1.0,
    "hf": 1.0,
    "ckd": 1.0,
    "atr_fibril": 0.6,
    "dm": 0.6,
    "copd": 0.5,
    "art_hyper": 0.4,
}


def _clean_text(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = _DATE_RX.sub(" ", text)
    text = _NUMBER_RX.sub(" ", text)
    # Administrative identifiers are frequent but are not semantic cluster features.
    text = re.sub(r"(?im)^\s*пациент(?:ка)?[^\n]*$", " ", text)
    text = re.sub(r"(?im)^.*история\s+болезни[^\n]*$", " ", text)
    text = re.sub(r"(?im)^\s*(?:фио|id|номер карты|номер истории)[^\n]*$", " ", text)
    return text


def _read_stopwords(path_value: str | None) -> set[str]:
    if not path_value:
        path = ROOT / "config" / "cluster_stopwords.txt"
    else:
        path = Path(path_value)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
    if not path.exists():
        return set()
    return {
        x.strip().lower().replace("ё", "е")
        for x in path.read_text(encoding="utf-8").splitlines()
        if x.strip() and not x.lstrip().startswith("#")
    }


def _high_confidence_value(preview: dict[str, Any], field: str, min_score: float) -> str | None:
    flat = preview.get("flat", {})
    audit = preview.get("audit", {})
    value = str(flat.get(field, NOT_SPEC))
    if value == NOT_SPEC:
        return None
    item = audit.get(field, {})
    if float(item.get("score", 0.0)) < min_score:
        return None
    if item.get("format_valid") is False or item.get("evidence_valid") is False:
        return None
    return value


def _clinical_similarity(a: dict, b: dict, weights: dict[str, float], min_score: float) -> tuple[float, float]:
    matched = total = 0.0
    for field, weight in weights.items():
        av = _high_confidence_value(a, field, min_score)
        bv = _high_confidence_value(b, field, min_score)
        if av is None or bv is None:
            continue
        total += weight
        if av == bv:
            matched += weight
    return (matched / total if total else 0.0), total


def _value_distribution(indices: list[int], previews: list[dict], field: str, min_score: float, exclude: int) -> dict[str, float]:
    counts: Counter[str] = Counter()
    total = 0
    for idx in indices:
        if idx == exclude:
            continue
        value = _high_confidence_value(previews[idx], field, min_score)
        if value is None:
            continue
        counts[value] += 1
        total += 1
    if not total:
        return {}
    return {value: count / total for value, count in counts.items()}


def _presence_consistency(flat: dict[str, str], peers: list[dict[str, str]]) -> float | None:
    if len(peers) < 2:
        return None
    values = []
    for field in _PRESENCE_FIELDS:
        p = sum(1 for peer in peers if str(peer.get(field, NOT_SPEC)) != NOT_SPEC) / len(peers)
        present = str(flat.get(field, NOT_SPEC)) != NOT_SPEC
        values.append(p if present else (1.0 - p))
    return sum(values) / len(values) if values else None


def _presence_vector(preview: dict[str, Any]) -> list[int]:
    flat = preview.get("flat", {})
    return [1 if str(flat.get(field, NOT_SPEC)) != NOT_SPEC else 0 for field in _PRESENCE_FIELDS]


def _mask_distance(a: list[int], b: list[int]) -> float:
    """Jaccard-like distance for extraction-presence masks."""
    union = sum(1 for x, y in zip(a, b) if x or y)
    if not union:
        return 0.0
    mismatch = sum(1 for x, y in zip(a, b) if (x or y) and x != y)
    return mismatch / union


def _semantic_distance(z, i: int, j: int) -> float:
    if z is None:
        return 0.0
    # Vectors are L2-normalized. Convert cosine distance to a bounded [0, 1] scale.
    dot = float((z[i] * z[j]).sum())
    dot = max(-1.0, min(1.0, dot))
    return max(0.0, min(1.0, (1.0 - dot) / 2.0))


class BatchSimilarityAnalyzer:
    """Optional batch-level cohort signal and calibration-sample selector.

    The analyzer runs on the whole batch before expensive extraction. It never
    creates a clinical value. It can only:
    - describe a batch (frequency analysis + HDBSCAN clusters),
    - choose a small representative/diverse calibration subset,
    - provide a weak prior for arbitration between already-supported candidates,
    - slightly adjust file confidence using peer consistency.
    """

    def __init__(self, cfg: dict | None):
        self.cfg = cfg or {}

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", False))

    def analyze(self, documents: list[dict[str, Any]], sampling_cfg: dict | None = None) -> dict[str, Any]:
        if not self.enabled or len(documents) < int(self.cfg.get("min_batch_documents", 5)):
            return {
                "enabled": False,
                "reason": "disabled_or_batch_too_small",
                "documents": {},
                "peer_priors": {},
                "frequency": {},
                "clusters": {},
                "clinical_neighbors": {},
                "probe_selection": {},
            }

        texts = [_clean_text(str(d.get("text", ""))) for d in documents]
        previews = [d.get("preview", {}) for d in documents]
        names = [str(d.get("document_id", f"document-{i+1}")) for i, d in enumerate(documents)]

        static_stop = _read_stopwords(self.cfg.get("stopwords_file"))
        df: Counter[str] = Counter()
        for text in texts:
            df.update(set(_TOKEN_RX.findall(text)))
        auto_threshold = float(self.cfg.get("auto_stopword_docfreq", 0.95))
        auto_stop = {word for word, count in df.items() if count / len(texts) >= auto_threshold}
        stop = (static_stop | auto_stop) - _PROTECTED_SEMANTIC_TOKENS

        result: dict[str, Any] = {
            "enabled": True,
            "method": "HDBSCAN(TF-IDF -> TruncatedSVD -> L2)",
            "documents": {},
            "peer_priors": {},
            "frequency": {
                "documents": len(texts),
                "auto_stopword_docfreq": auto_threshold,
                "auto_stopwords": sorted(auto_stop),
                "top_document_frequency": [
                    {"term": w, "documents": int(c), "fraction": round(c / len(texts), 4)}
                    for w, c in df.most_common(int(self.cfg.get("frequency_top_terms", 80)))
                ],
            },
            "clusters": {},
            "clinical_neighbors": {},
            "probe_selection": {},
        }

        labels = [-1] * len(texts)
        probabilities = [0.0] * len(texts)
        X = None
        terms = None
        Z = None
        try:
            from sklearn.cluster import HDBSCAN
            from sklearn.decomposition import TruncatedSVD
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.preprocessing import Normalizer

            vectorizer = TfidfVectorizer(
                stop_words=sorted(stop),
                max_features=int(self.cfg.get("max_features", 10000)),
                ngram_range=(int(self.cfg.get("ngram_min", 1)), int(self.cfg.get("ngram_max", 2))),
                min_df=int(self.cfg.get("min_df", 2)),
                max_df=float(self.cfg.get("max_df", 0.92)),
                sublinear_tf=True,
            )
            X = vectorizer.fit_transform(texts)
            terms = vectorizer.get_feature_names_out()
            if X.shape[1] >= 2 and len(texts) >= 3:
                n_components = max(2, min(int(self.cfg.get("svd_components", 30)), X.shape[0] - 1, X.shape[1] - 1))
                Z = TruncatedSVD(
                    n_components=n_components,
                    random_state=int(self.cfg.get("random_state", 42)),
                ).fit_transform(X)
                Z = Normalizer().fit_transform(Z)
                min_cluster = min(
                    max(2, int(self.cfg.get("min_cluster_size", 5))),
                    max(2, len(texts) - 1),
                )
                model = HDBSCAN(
                    min_cluster_size=min_cluster,
                    min_samples=max(1, int(self.cfg.get("min_samples", 2))),
                    metric="euclidean",
                    cluster_selection_method=str(self.cfg.get("cluster_selection_method", "eom")),
                    allow_single_cluster=bool(self.cfg.get("allow_single_cluster", False)),
                    copy=True,
                ).fit(Z)
                labels = [int(x) for x in model.labels_]
                probabilities = [float(x) for x in getattr(model, "probabilities_", [0.0] * len(texts))]
        except Exception as exc:
            result["cluster_error"] = repr(exc)

        clusters: dict[int, list[int]] = defaultdict(list)
        for idx, label in enumerate(labels):
            if label >= 0:
                clusters[label].append(idx)
            result["documents"][names[idx]] = {
                "cluster": label,
                "cluster_probability": round(probabilities[idx], 4),
                "deterministic_presence_count": sum(_presence_vector(previews[idx])),
            }

        # Interpretable cluster summaries from original TF-IDF space.
        for label, indices in sorted(clusters.items()):
            top_terms: list[dict[str, Any]] = []
            if X is not None and terms is not None and indices:
                centroid = X[indices].mean(axis=0)
                arr = centroid.A1
                for j in arr.argsort()[::-1][: int(self.cfg.get("cluster_top_terms", 20))]:
                    if arr[j] <= 0:
                        continue
                    top_terms.append({"term": str(terms[j]), "weight": round(float(arr[j]), 5)})
            result["clusters"][str(label)] = {"size": len(indices), "top_terms": top_terms}

        # Choose calibration probes only after the whole batch has been clustered.
        result["probe_selection"] = self._select_probes(
            names=names,
            previews=previews,
            labels=labels,
            probabilities=probabilities,
            clusters=clusters,
            Z=Z,
            sampling_cfg=sampling_cfg or {},
        )

        min_preview_score = float(self.cfg.get("peer_min_preview_score", 0.90))
        weights = dict(_DEFAULT_CLINICAL_WEIGHTS)
        weights.update({k: float(v) for k, v in (self.cfg.get("clinical_field_weights", {}) or {}).items()})
        clinical_threshold = float(self.cfg.get("clinical_similarity_threshold", 0.72))
        min_shared_weight = float(self.cfg.get("clinical_min_shared_weight", 3.0))
        max_neighbors = int(self.cfg.get("clinical_max_neighbors", 12))

        clinical_neighbors: dict[int, list[tuple[int, float]]] = {i: [] for i in range(len(documents))}
        for i in range(len(documents)):
            scored = []
            for j in range(len(documents)):
                if i == j:
                    continue
                sim, shared = _clinical_similarity(previews[i], previews[j], weights, min_preview_score)
                if shared >= min_shared_weight and sim >= clinical_threshold:
                    scored.append((j, sim))
            scored.sort(key=lambda x: x[1], reverse=True)
            clinical_neighbors[i] = scored[:max_neighbors]
            result["clinical_neighbors"][names[i]] = [
                {"document_id": names[j], "similarity": round(sim, 4)} for j, sim in clinical_neighbors[i]
            ]

        cluster_weight = float(self.cfg.get("peer_cluster_prior_weight", 0.55))
        clinical_weight = float(self.cfg.get("peer_clinical_prior_weight", 0.45))
        for i, name in enumerate(names):
            prior: dict[str, dict[str, float]] = {}
            cluster_indices = clusters.get(labels[i], []) if labels[i] >= 0 else []
            clinical_indices = [j for j, _ in clinical_neighbors[i]]
            clinical_sim = {j: sim for j, sim in clinical_neighbors[i]}

            for field in ALL_FIELDS:
                cluster_dist = _value_distribution(cluster_indices, previews, field, min_preview_score, i)
                clin_counts: Counter[str] = Counter()
                clin_total = 0.0
                for j in clinical_indices:
                    value = _high_confidence_value(previews[j], field, min_preview_score)
                    if value is None:
                        continue
                    w = clinical_sim[j]
                    clin_counts[value] += w
                    clin_total += w
                clinical_dist = {k: v / clin_total for k, v in clin_counts.items()} if clin_total else {}

                values = set(cluster_dist) | set(clinical_dist)
                if not values:
                    continue
                cw = cluster_weight if cluster_dist else 0.0
                hw = clinical_weight if clinical_dist else 0.0
                denom = cw + hw
                if denom <= 0:
                    continue
                combined = {
                    value: (cw * cluster_dist.get(value, 0.0) + hw * clinical_dist.get(value, 0.0)) / denom
                    for value in values
                }
                prior[field] = {k: round(float(v), 4) for k, v in combined.items() if v > 0}
            result["peer_priors"][name] = prior

        return result

    def _select_probes(
        self,
        names: list[str],
        previews: list[dict],
        labels: list[int],
        probabilities: list[float],
        clusters: dict[int, list[int]],
        Z,
        sampling_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        n = len(names)
        budget = min(n, max(0, int(sampling_cfg.get("probe_files", sampling_cfg.get("warmup_files", 5)))))
        if budget <= 0:
            return {"budget": 0, "selected_indices": [], "selected_document_ids": [], "items": []}

        semantic_w = float(sampling_cfg.get("sampling_semantic_weight", 0.65))
        mask_w = float(sampling_cfg.get("sampling_extraction_mask_weight", 0.35))
        total_w = max(1e-9, semantic_w + mask_w)
        semantic_w, mask_w = semantic_w / total_w, mask_w / total_w
        masks = [_presence_vector(p) for p in previews]

        def distance(i: int, j: int) -> float:
            return semantic_w * _semantic_distance(Z, i, j) + mask_w * _mask_distance(masks[i], masks[j])

        selected: list[int] = []
        meta: dict[int, dict[str, Any]] = {}

        # 1) Representatives: real medoids from the largest clusters.
        reserve_for_novelty = min(2, max(0, budget - 1))
        representative_slots = min(len(clusters), max(1, budget - reserve_for_novelty)) if clusters else 0
        largest_clusters = sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:representative_slots]
        for label, indices in largest_clusters:
            if not indices:
                continue
            medoid = min(
                indices,
                key=lambda i: sum(distance(i, j) for j in indices if j != i) / max(1, len(indices) - 1),
            )
            if medoid not in selected:
                selected.append(medoid)
                meta[medoid] = {
                    "role": "representative_medoid",
                    "cluster": label,
                    "cluster_size": len(indices),
                    "cluster_probability": round(float(probabilities[medoid]), 4),
                }
            if len(selected) >= budget:
                break

        # 2) Template-shift-sensitive document: semantically within a cluster but
        # unusually sparse/different deterministic extraction mask.
        if len(selected) < budget and bool(sampling_cfg.get("sampling_include_template_shift", True)):
            best_idx = None
            best_score = -1.0
            best_parts = None
            for i in range(n):
                if i in selected:
                    continue
                label = labels[i]
                if label >= 0 and clusters.get(label):
                    members = clusters[label]
                    cluster_coverages = [sum(masks[j]) for j in members]
                    expected = float(median(cluster_coverages)) if cluster_coverages else 0.0
                    deficit = max(0.0, (expected - sum(masks[i])) / max(1.0, expected))
                    # Compare with the closest representative/medoid of this cluster.
                    medoid_candidates = [j for j in selected if labels[j] == label]
                    if medoid_candidates:
                        mask_novelty = max(_mask_distance(masks[i], masks[j]) for j in medoid_candidates)
                    else:
                        mask_novelty = sum(_mask_distance(masks[i], masks[j]) for j in members if j != i) / max(1, len(members) - 1)
                    low_membership = 1.0 - max(0.0, min(1.0, float(probabilities[i])))
                    score = 0.55 * deficit + 0.35 * mask_novelty + 0.10 * low_membership
                else:
                    # Noise can be useful, but do not let a single extreme outlier dominate.
                    deficit = max(0.0, 1.0 - sum(masks[i]) / max(1.0, median([sum(m) for m in masks]) or 1.0))
                    mask_novelty = 0.0
                    if selected:
                        mask_novelty = min(_mask_distance(masks[i], masks[j]) for j in selected)
                    low_membership = 1.0
                    score = float(sampling_cfg.get("sampling_noise_template_penalty", 0.75)) * (
                        0.55 * deficit + 0.35 * mask_novelty + 0.10
                    )
                if score > best_score:
                    best_idx, best_score = i, score
                    best_parts = (deficit, mask_novelty, low_membership)
            if best_idx is not None:
                selected.append(best_idx)
                deficit, mask_novelty, low_membership = best_parts or (0.0, 0.0, 0.0)
                meta[best_idx] = {
                    "role": "template_shift_candidate",
                    "cluster": labels[best_idx],
                    "score": round(best_score, 4),
                    "coverage_deficit": round(deficit, 4),
                    "mask_novelty": round(mask_novelty, 4),
                    "low_cluster_membership": round(low_membership, 4),
                }

        # 3) Fill the remaining budget by farthest-first traversal. This maximizes
        # coverage of text + extraction-pattern diversity without guessing K.
        while len(selected) < budget:
            candidates = [i for i in range(n) if i not in selected]
            if not candidates:
                break
            if not selected:
                # Fallback if HDBSCAN produced no usable clusters.
                chosen = max(candidates, key=lambda i: sum(distance(i, j) for j in candidates if j != i))
                min_dist = 0.0
            else:
                scored = []
                for i in candidates:
                    min_dist = min(distance(i, j) for j in selected)
                    if labels[i] < 0:
                        min_dist *= float(sampling_cfg.get("sampling_noise_diversity_penalty", 0.90))
                    scored.append((min_dist, i))
                min_dist, chosen = max(scored)
            selected.append(chosen)
            meta[chosen] = {
                "role": "diversity_farthest",
                "cluster": labels[chosen],
                "min_distance_to_selected": round(float(min_dist), 4),
                "cluster_probability": round(float(probabilities[chosen]), 4),
            }

        items = [
            {"index": i, "document_id": names[i], **meta.get(i, {"role": "selected"})}
            for i in selected
        ]
        return {
            "budget": budget,
            "method": "cluster_medoids + template_shift + farthest_first",
            "distance": {
                "semantic_weight": round(semantic_w, 4),
                "extraction_mask_weight": round(mask_w, 4),
            },
            "selected_indices": selected,
            "selected_document_ids": [names[i] for i in selected],
            "items": items,
        }

    def score_consistency(
        self,
        document_id: str,
        flat_by_document: dict[str, dict[str, str]],
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        if not analysis.get("enabled") or document_id not in flat_by_document:
            return {"available": False}

        meta = analysis.get("documents", {}).get(document_id, {})
        cluster = int(meta.get("cluster", -1))
        flat = flat_by_document[document_id]

        cluster_peers = [
            peer_flat for peer_id, peer_flat in flat_by_document.items()
            if peer_id != document_id
            and cluster >= 0
            and int(analysis.get("documents", {}).get(peer_id, {}).get("cluster", -2)) == cluster
        ]
        clinical_ids = {
            x.get("document_id") for x in analysis.get("clinical_neighbors", {}).get(document_id, [])
            if x.get("document_id") in flat_by_document
        }
        clinical_peers = [flat_by_document[x] for x in clinical_ids if x != document_id]

        cluster_consistency = _presence_consistency(flat, cluster_peers)
        clinical_consistency = _presence_consistency(flat, clinical_peers)
        return {
            "available": cluster_consistency is not None or clinical_consistency is not None,
            "cluster": cluster,
            "cluster_probability": float(meta.get("cluster_probability", 0.0)),
            "cluster_peer_count": len(cluster_peers),
            "clinical_peer_count": len(clinical_peers),
            "cluster_presence_consistency": None if cluster_consistency is None else round(cluster_consistency, 4),
            "clinical_presence_consistency": None if clinical_consistency is None else round(clinical_consistency, 4),
        }

    def adjust_score(self, base_score: float, consistency: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        if not consistency.get("available"):
            return base_score, {"adjustment": 0.0, **consistency}

        cluster_w = float(self.cfg.get("score_cluster_presence_weight", 0.025))
        clinical_w = float(self.cfg.get("score_clinical_presence_weight", 0.025))
        reference = float(self.cfg.get("score_consistency_reference", 0.85))

        def centered(value: float) -> float:
            # 0 at the expected within-cohort consistency; bounded to [-1, 1].
            if value >= reference:
                return min(1.0, (value - reference) / max(1e-9, 1.0 - reference))
            return max(-1.0, (value - reference) / max(1e-9, reference))

        adjustment = 0.0
        cc = consistency.get("cluster_presence_consistency")
        hc = consistency.get("clinical_presence_consistency")
        if cc is not None:
            # HDBSCAN probability suppresses unreliable cluster influence.
            adjustment += cluster_w * centered(float(cc)) * float(consistency.get("cluster_probability", 0.0))
        if hc is not None:
            adjustment += clinical_w * centered(float(hc))
        max_negative = abs(float(self.cfg.get("score_max_negative_adjustment", self.cfg.get("score_max_abs_adjustment", 0.05))))
        max_positive = max(0.0, float(self.cfg.get("score_max_positive_adjustment", 0.0)))
        adjustment = max(-max_negative, min(max_positive, adjustment))
        score = max(0.0, min(1.0, float(base_score) + adjustment))
        return round(score, 4), {"adjustment": round(adjustment, 4), **consistency}
