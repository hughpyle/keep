#!/usr/bin/env python3
"""Probe whether embedding geometry exposes a useful hidden basis.

This script sits beside ``reconstruct_spike.py`` and reuses its probe
sampling and candidate-pool construction, but asks a different set of
questions:

1. Does true projection-residual OMP over candidate embeddings recover
   good support for `towards` concerns?
2. Are local candidate pools and induced groups compressible in
   embedding space?
3. After sparse selection, is the remaining residual concentrated in a
   small number of structural regions?
4. Do version-change vectors live in a low-dimensional local subspace?

The point is not to prove a final algorithm. The point is to see
whether the embedding geometry contains enough structure to justify a
less text-driven reconstruction path.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reconstruct_spike import (  # type: ignore
    Keeper,
    Probe,
    _base_id,
    _candidate_pool_upper_bounds,
    _load_candidate_embeddings_batch,
    _support_metrics,
    _token_set,
    _probe_rows,
    build_candidate_pool,
    build_group_candidates,
    recipe_config,
    run_reconstruction,
    run_grouped_reconstruction,
    sample_edge_probes,
    sample_part_probes,
    sample_qa_probes,
    sample_version_probes,
)


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def _effective_rank(singular_values: np.ndarray) -> float:
    singular_values = singular_values[singular_values > 0]
    if singular_values.size == 0:
        return 0.0
    probs = singular_values / singular_values.sum()
    entropy = -float(np.sum(probs * np.log(probs)))
    return float(math.exp(entropy))


def _spectral_stats(matrix: np.ndarray) -> dict[str, float]:
    if matrix.shape[0] < 2 or matrix.shape[1] < 2:
        return {
            "n": float(matrix.shape[0]),
            "effective_rank": 0.0,
            "pc1_explained": 1.0 if matrix.shape[0] == 1 else 0.0,
            "pc2_explained": 1.0 if matrix.shape[0] <= 2 else 0.0,
        }
    centered = matrix - matrix.mean(axis=0, keepdims=True)
    singular_values = np.linalg.svd(centered, compute_uv=False)
    energy = singular_values ** 2
    total = float(energy.sum())
    if total <= 0.0:
        return {
            "n": float(matrix.shape[0]),
            "effective_rank": 0.0,
            "pc1_explained": 0.0,
            "pc2_explained": 0.0,
        }
    pc1 = float(energy[0] / total)
    pc2 = float(energy[:2].sum() / total) if energy.size >= 2 else pc1
    return {
        "n": float(matrix.shape[0]),
        "effective_rank": _effective_rank(singular_values),
        "pc1_explained": pc1,
        "pc2_explained": pc2,
    }


def _candidate_matrix(candidates: dict[str, Any]) -> tuple[list[str], np.ndarray]:
    ids: list[str] = []
    rows: list[list[float]] = []
    for cand_id, cand in candidates.items():
        if cand.embedding is None:
            continue
        ids.append(cand_id)
        rows.append(list(cand.embedding))
    if not rows:
        return [], np.zeros((0, 0), dtype=float)
    return ids, _normalize_rows(np.asarray(rows, dtype=float))


def _vector_for_id(ids: list[str], matrix: np.ndarray, target_id: str | None) -> np.ndarray | None:
    if target_id is None:
        return None
    try:
        idx = ids.index(target_id)
    except ValueError:
        return None
    return matrix[idx]


def _projection_omp(
    signal: np.ndarray,
    ids: list[str],
    matrix: np.ndarray,
    *,
    support_limit: int,
    min_corr: float = 1e-6,
    residual_threshold: float = 0.12,
) -> dict[str, Any]:
    """True projection-residual OMP over candidate embeddings.

    This is the cleanest embedding-only test in the harness. It ignores
    lexical and tag features and asks: if the concern really is sparse
    in the candidate embeddings, does projection residual recover a good
    support?
    """
    if matrix.size == 0 or signal.size == 0:
        return {
            "support_ids": [],
            "steps": [],
            "residual_ratio": 1.0,
        }

    selected: list[int] = []
    residual = signal.copy()
    steps: list[dict[str, Any]] = []
    signal_norm = float(np.linalg.norm(signal)) or 1.0

    for step_num in range(1, support_limit + 1):
        correlations = matrix @ residual
        for idx in selected:
            correlations[idx] = -1.0
        best_idx = int(np.argmax(correlations))
        best_corr = float(correlations[best_idx])
        if best_corr < min_corr:
            break

        selected.append(best_idx)
        basis = matrix[selected].T
        coeffs, *_ = np.linalg.lstsq(basis, signal, rcond=None)
        reconstruction = basis @ coeffs
        residual = signal - reconstruction
        residual_ratio = float(np.linalg.norm(residual) / signal_norm)
        steps.append({
            "step": step_num,
            "selected_id": ids[best_idx],
            "correlation": round(best_corr, 6),
            "residual_ratio": round(residual_ratio, 6),
        })
        if residual_ratio < residual_threshold:
            break

    support_ids = [ids[idx] for idx in selected]
    return {
        "support_ids": support_ids,
        "steps": steps,
        "residual_ratio": steps[-1]["residual_ratio"] if steps else 1.0,
    }


def _greedy_representative_coverage(matrix: np.ndarray, *, support_limit: int) -> list[dict[str, float]]:
    """Greedy representative subset selection over cosine similarity.

    This is not DS3 proper. It is a cheap representative-selection probe
    asking whether a few rows cover the rest of the neighborhood in
    embedding space.
    """
    if matrix.size == 0:
        return []
    sim = np.clip(matrix @ matrix.T, 0.0, 1.0)
    selected: list[int] = []
    coverage = np.zeros(matrix.shape[0], dtype=float)
    steps: list[dict[str, float]] = []

    for step_num in range(1, min(support_limit, matrix.shape[0]) + 1):
        best_idx = None
        best_gain = -1.0
        best_next = coverage
        for idx in range(matrix.shape[0]):
            if idx in selected:
                continue
            next_cov = np.maximum(coverage, sim[idx])
            gain = float(next_cov.mean() - coverage.mean())
            if gain > best_gain:
                best_idx = idx
                best_gain = gain
                best_next = next_cov
        if best_idx is None:
            break
        selected.append(best_idx)
        coverage = best_next
        steps.append({
            "k": float(step_num),
            "mean_nearest_cosine": float(coverage.mean()),
            "min_nearest_cosine": float(coverage.min()),
            "gain": best_gain,
        })
    return steps


def _mass_concentration(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {"top1_share": 0.0, "hhi": 0.0, "entropy": 0.0, "count": 0.0}
    total = sum(max(value, 0.0) for value in scores.values())
    if total <= 0.0:
        return {"top1_share": 0.0, "hhi": 0.0, "entropy": 0.0, "count": float(len(scores))}
    probs = [max(value, 0.0) / total for value in scores.values()]
    top1 = max(probs)
    hhi = sum(p * p for p in probs)
    entropy = -sum(p * math.log(p) for p in probs if p > 0)
    return {
        "top1_share": float(top1),
        "hhi": float(hhi),
        "entropy": float(entropy),
        "count": float(len(scores)),
    }


def _residual_concentration(
    signal: np.ndarray,
    ids: list[str],
    matrix: np.ndarray,
    support_ids: list[str],
    *,
    groups: list[Any] | None = None,
) -> dict[str, Any]:
    if matrix.size == 0 or signal.size == 0:
        return {}
    selected = [ids.index(item_id) for item_id in support_ids if item_id in ids]
    if selected:
        basis = matrix[selected].T
        coeffs, *_ = np.linalg.lstsq(basis, signal, rcond=None)
        residual = signal - basis @ coeffs
    else:
        residual = signal.copy()
    candidate_scores = np.clip(matrix @ residual, 0.0, None)

    base_scores: dict[str, float] = defaultdict(float)
    for idx, cand_id in enumerate(ids):
        base_scores[_base_id(cand_id)] += float(candidate_scores[idx])
    result = {
        "base": _mass_concentration(base_scores),
    }

    if groups:
        group_scores: dict[str, float] = {}
        index_by_id = {cand_id: idx for idx, cand_id in enumerate(ids)}
        for group in groups:
            score = 0.0
            for member_id in group.member_ids:
                idx = index_by_id.get(member_id)
                if idx is not None:
                    score += float(candidate_scores[idx])
            group_scores[group.group_id] = score
        result["group"] = _mass_concentration(group_scores)
    return result


def _submatrix_for_members(ids: list[str], matrix: np.ndarray, member_ids: list[str]) -> tuple[list[str], np.ndarray]:
    index_by_id = {cand_id: idx for idx, cand_id in enumerate(ids)}
    chosen_ids: list[str] = []
    rows: list[np.ndarray] = []
    for member_id in member_ids:
        idx = index_by_id.get(member_id)
        if idx is None:
            continue
        chosen_ids.append(member_id)
        rows.append(matrix[idx])
    if not rows:
        return [], np.zeros((0, 0), dtype=float)
    return chosen_ids, np.asarray(rows, dtype=float)


def _local_group_probe(
    signal: np.ndarray,
    ids: list[str],
    matrix: np.ndarray,
    group: Any,
    *,
    support_limit: int,
    target_ids: list[str],
) -> dict[str, Any]:
    member_ids, member_matrix = _submatrix_for_members(ids, matrix, group.member_ids)
    if member_matrix.size == 0:
        return {
            "group_id": group.group_id,
            "kind": group.kind,
            "score_measurement": float(group.score_measurement),
            "member_count": 0,
        }
    spectral = _spectral_stats(member_matrix)
    reps = _greedy_representative_coverage(member_matrix, support_limit=min(4, member_matrix.shape[0]))
    omp = _projection_omp(signal, member_ids, member_matrix, support_limit=min(support_limit, member_matrix.shape[0]))
    omp["metrics"] = _support_metrics(omp["support_ids"], target_ids)
    return {
        "group_id": group.group_id,
        "kind": group.kind,
        "score_measurement": float(group.score_measurement),
        "member_count": len(member_ids),
        "spectral": spectral,
        "rep_steps": reps,
        "omp_metrics": omp["metrics"],
        "omp_residual_ratio": omp.get("residual_ratio", 1.0),
    }


def _structural_group_selector_score(
    group: Any,
    *,
    spectral: dict[str, float],
    rep_steps: list[dict[str, float]],
) -> tuple[float, dict[str, float]]:
    """Rank local neighborhoods for embedding-only reconstruction.

    This is the non-oracle selector we actually care about. It must
    choose a group using only signals available at selection time:

    - existing measurement strength from the mixed retrieval path
    - local embedding compactness inside the induced neighborhood
    - a mild prior against edge-local bundles, which have been the least
      compressible group family in the current experiments

    The point is not to freeze a product formula. The point is to test
    whether embedding reconstruction gets materially better when we
    first choose a neighborhood that looks locally span-friendly.
    """
    rep2 = rep_steps[min(1, len(rep_steps) - 1)]["mean_nearest_cosine"] if rep_steps else 0.0
    compactness = (
        0.45 * rep2
        + 0.35 * spectral.get("pc1_explained", 0.0)
        + 0.20 * spectral.get("pc2_explained", 0.0)
    )
    rank_penalty = 1.0 / (1.0 + math.log1p(max(spectral.get("effective_rank", 0.0), 0.0)))
    kind_prior = {
        "lineage-local": 1.15,
        "part-window": 1.10,
        "base-local": 1.00,
        "edge-local": 0.55,
    }.get(str(getattr(group, "kind", "")), 1.0)
    measurement = max(float(getattr(group, "score_measurement", 0.0)), 0.0)
    score = measurement * compactness * rank_penalty * kind_prior
    return score, {
        "measurement": round(measurement, 6),
        "compactness": round(compactness, 6),
        "rank_penalty": round(rank_penalty, 6),
        "kind_prior": round(kind_prior, 6),
    }


def _support_seeded_group_selector_score(
    group: Any,
    *,
    spectral: dict[str, float],
    rep_steps: list[dict[str, float]],
    support_ids: list[str],
) -> tuple[float, dict[str, float]]:
    """Choose a local neighborhood using coarse mixed-feature support.

    This is still non-oracle: it does not look at targets. It simply
    says that if mixed retrieval has already found a few plausible notes,
    the local embedding neighborhood should be anchored on those notes or
    their bases rather than on an arbitrary compact cluster elsewhere.
    """
    support_set = set(support_ids)
    support_bases = {_base_id(item_id) for item_id in support_ids}
    member_set = set(getattr(group, "member_ids", []))
    emit_set = set(getattr(group, "emit_ids", []))
    group_bases = set(getattr(group, "base_ids", set()))

    member_overlap = len(member_set & support_set) / max(len(support_set), 1)
    emit_overlap = len(emit_set & support_set) / max(len(support_set), 1)
    base_overlap = len(group_bases & support_bases) / max(len(support_bases), 1)
    if member_overlap <= 0.0 and base_overlap <= 0.0:
        return 0.0, {
            "member_overlap": 0.0,
            "emit_overlap": 0.0,
            "base_overlap": 0.0,
            "compactness": 0.0,
            "kind_prior": 0.0,
        }

    rep2 = rep_steps[min(1, len(rep_steps) - 1)]["mean_nearest_cosine"] if rep_steps else 0.0
    compactness = (
        0.50 * rep2
        + 0.30 * spectral.get("pc1_explained", 0.0)
        + 0.20 * spectral.get("pc2_explained", 0.0)
    )
    kind_prior = {
        "part-window": 1.10,
        "base-local": 1.00,
        "lineage-local": 0.95,
        "edge-local": 0.70,
    }.get(str(getattr(group, "kind", "")), 1.0)
    coverage = 2.0 * member_overlap + 1.5 * emit_overlap + 1.0 * base_overlap
    score = coverage * compactness * kind_prior
    return score, {
        "member_overlap": round(member_overlap, 6),
        "emit_overlap": round(emit_overlap, 6),
        "base_overlap": round(base_overlap, 6),
        "compactness": round(compactness, 6),
        "kind_prior": round(kind_prior, 6),
    }


def _version_lineage_rows(kp: Keeper, *, min_versions: int) -> list[dict[str, Any]]:
    doc_store = kp._document_store
    coll = kp._resolve_doc_collection()
    return _probe_rows(
        doc_store,
        """
        SELECT v.id, COUNT(1) AS version_count
        FROM document_versions v
        WHERE v.collection = ?
        GROUP BY v.id
        HAVING COUNT(1) >= ?
        """,
        (coll, min_versions),
    )


def _version_ids_for_base(kp: Keeper, base_id: str) -> list[str]:
    doc_store = kp._document_store
    coll = kp._resolve_doc_collection()
    rows = _probe_rows(
        doc_store,
        """
        SELECT version
        FROM document_versions
        WHERE collection = ? AND id = ?
        ORDER BY version
        """,
        (coll, base_id),
    )
    return [f"{base_id}@v{int(row['version'])}" for row in rows]


def _embedding_map(kp: Keeper, ids: list[str]) -> dict[str, np.ndarray]:
    coll = kp._resolve_chroma_collection()
    entries = kp._store.get_entries_full(coll, ids)
    out: dict[str, np.ndarray] = {}
    for entry in entries:
        emb = entry.get("embedding")
        if emb is None:
            continue
        out[str(entry["id"])] = np.asarray(emb, dtype=float)
    return out


def _version_delta_stats(kp: Keeper, rng: random.Random, *, sample_size: int, min_versions: int) -> dict[str, Any]:
    rows = _version_lineage_rows(kp, min_versions=min_versions)
    rng.shuffle(rows)
    selected = rows[:sample_size]
    lineage_stats: list[dict[str, Any]] = []
    all_diff_vectors: list[np.ndarray] = []
    lineage_lengths: list[int] = []

    for row in selected:
        base_id = str(row["id"])
        version_ids = _version_ids_for_base(kp, base_id)
        emb_map = _embedding_map(kp, version_ids)
        ordered = [emb_map[vid] for vid in version_ids if vid in emb_map]
        if len(ordered) < min_versions:
            continue
        diffs: list[np.ndarray] = []
        for prev, curr in zip(ordered, ordered[1:]):
            diff = curr - prev
            norm = float(np.linalg.norm(diff))
            if norm <= 1e-9:
                continue
            diffs.append(diff / norm)
        if len(diffs) < 2:
            continue
        diff_matrix = np.asarray(diffs, dtype=float)
        stats = _spectral_stats(diff_matrix)
        stats["base_id"] = base_id
        stats["n_diffs"] = float(diff_matrix.shape[0])
        lineage_stats.append(stats)
        all_diff_vectors.extend(diff_matrix)
        lineage_lengths.append(diff_matrix.shape[0])

    random_baseline: list[float] = []
    if len(all_diff_vectors) >= 4 and lineage_lengths:
        all_matrix = np.asarray(all_diff_vectors, dtype=float)
        for length in lineage_lengths:
            if length < 2 or length > all_matrix.shape[0]:
                continue
            indices = rng.sample(range(all_matrix.shape[0]), length)
            random_stats = _spectral_stats(all_matrix[indices])
            random_baseline.append(random_stats["pc1_explained"])

    return {
        "sample_count": len(lineage_stats),
        "pc1_explained_mean": _mean([row["pc1_explained"] for row in lineage_stats]),
        "pc2_explained_mean": _mean([row["pc2_explained"] for row in lineage_stats]),
        "effective_rank_mean": _mean([row["effective_rank"] for row in lineage_stats]),
        "random_pc1_explained_mean": _mean(random_baseline),
        "lineages": lineage_stats[:50],
    }


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _load_probes(
    kp: Keeper,
    rng: random.Random,
    *,
    families: list[str],
    probes_per_family: int,
    qa_dataset: Path | None,
    qa_mode: str,
) -> list[Probe]:
    probes: list[Probe] = []
    builders = {
        "version": sample_version_probes,
        "part": sample_part_probes,
        "edge": sample_edge_probes,
    }
    for family in families:
        if family == "qa":
            if qa_dataset is None:
                raise SystemExit("--qa-dataset is required for family qa")
            probes.extend(sample_qa_probes(qa_dataset, rng, probes_per_family, mode=qa_mode, categories=set()))
            continue
        probes.extend(builders[family](kp, rng, probes_per_family))
    return probes


def _probe_experiment(
    kp: Keeper,
    probe: Probe,
    *,
    query_note_model: bool,
    semantic_limit: int,
    fts_limit: int,
    seed_limit: int,
    edge_limit: int,
    version_limit: int,
    part_limit: int,
    support_limit: int,
) -> dict[str, Any]:
    candidates, _semantic_rows, _fts_rows, ctx = build_candidate_pool(
        kp,
        probe,
        semantic_limit=semantic_limit,
        fts_limit=fts_limit,
        seed_limit=seed_limit,
        edge_limit=edge_limit,
        version_limit=version_limit,
        part_limit=part_limit,
        query_note_model=query_note_model,
    )
    recipe = recipe_config(probe)
    flat = run_reconstruction(
        candidates,
        probe,
        weights=recipe["weights"],
        support_limit=support_limit,
        min_effective=recipe["min_effective"],
        redundancy_weight=recipe["redundancy_weight"],
        same_base_penalty=recipe["same_base_penalty"],
        residual_threshold=recipe["residual_threshold"],
        new_anchor_bonus=recipe["new_anchor_bonus"],
        new_base_bonus=recipe["new_base_bonus"],
    )
    grouped_metrics = None
    if probe.mode == "around":
        grouped = run_grouped_reconstruction(
            candidates,
            probe,
            weights=recipe["weights"],
            query_note=ctx.get("query_note"),
            support_limit=support_limit,
            emit_limit=recipe["group_emit_limit"],
            min_effective=recipe["group_min_effective"],
            max_focus_demands=recipe["group_max_focus_demands"],
            max_region_demands=recipe["group_max_region_demands"],
            focus_demand_weight=recipe["group_focus_demand_weight"],
            anchor_demand_weight=recipe["group_anchor_demand_weight"],
            temporal_demand_weight=recipe["group_temporal_demand_weight"],
            region_demand_weight=recipe["group_region_demand_weight"],
            measurement_weight=recipe["group_measurement_weight"],
            overlap_penalty=recipe["group_overlap_penalty"],
            size_penalty=recipe["group_size_penalty"],
            residual_threshold=recipe["residual_threshold"],
        )
        grouped_metrics = grouped["metrics"]

    ids, matrix = _candidate_matrix(candidates)
    query_embedding = ctx.get("query_embedding")
    signal = np.asarray(query_embedding, dtype=float) if query_embedding is not None else np.zeros((0,), dtype=float)
    signal = signal / (np.linalg.norm(signal) or 1.0) if signal.size else signal
    omp = _projection_omp(signal, ids, matrix, support_limit=support_limit)
    omp["metrics"] = _support_metrics(omp["support_ids"], probe.target_ids)

    pool_stats = _spectral_stats(matrix)
    rep_steps = _greedy_representative_coverage(matrix, support_limit=min(support_limit, 6))

    group_summary: dict[str, list[dict[str, float]]] = defaultdict(list)
    groups = build_group_candidates(
        candidates,
        probe,
        weights=recipe["weights"],
        query_note=ctx.get("query_note"),
        emit_limit=recipe["group_emit_limit"],
    )
    top_group_probe = None
    selected_group_probe = None
    seeded_group_probe = None
    target_group_probe = None
    ranked_group_rows: list[tuple[float, Any, dict[str, float], list[dict[str, float]]]] = []
    seeded_group_rows: list[tuple[float, Any, dict[str, float], list[dict[str, float]]]] = []
    for group in groups:
        member_ids, member_matrix = _submatrix_for_members(ids, matrix, group.member_ids)
        if member_matrix.shape[0] < 3:
            continue
        stats = _spectral_stats(member_matrix)
        reps = _greedy_representative_coverage(member_matrix, support_limit=min(4, len(member_ids)))
        if reps:
            stats["rep2_mean_nearest"] = reps[min(1, len(reps) - 1)]["mean_nearest_cosine"]
        group_summary[group.kind].append(stats)
        selector_score, selector_diag = _structural_group_selector_score(
            group,
            spectral=stats,
            rep_steps=reps,
        )
        ranked_group_rows.append((selector_score, group, selector_diag, reps))
        seeded_score, seeded_diag = _support_seeded_group_selector_score(
            group,
            spectral=stats,
            rep_steps=reps,
            support_ids=flat["support_ids"],
        )
        seeded_group_rows.append((seeded_score, group, seeded_diag, reps))

    if groups and signal.size:
        ranked_groups = sorted(groups, key=lambda group: group.score_measurement, reverse=True)
        for group in ranked_groups:
            if len(group.member_ids) >= 3:
                top_group_probe = _local_group_probe(
                    signal,
                    ids,
                    matrix,
                    group,
                    support_limit=support_limit,
                    target_ids=probe.target_ids,
                )
                break
        ranked_group_rows.sort(key=lambda row: row[0], reverse=True)
        for selector_score, group, selector_diag, _reps in ranked_group_rows:
            if len(group.member_ids) < 3:
                continue
            selected_group_probe = _local_group_probe(
                signal,
                ids,
                matrix,
                group,
                support_limit=support_limit,
                target_ids=probe.target_ids,
            )
            selected_group_probe["selector_score"] = round(selector_score, 6)
            selected_group_probe["selector_diagnostics"] = selector_diag
            break
        seeded_group_rows.sort(key=lambda row: row[0], reverse=True)
        for selector_score, group, selector_diag, _reps in seeded_group_rows:
            if selector_score <= 0.0 or len(group.member_ids) < 3:
                continue
            seeded_group_probe = _local_group_probe(
                signal,
                ids,
                matrix,
                group,
                support_limit=support_limit,
                target_ids=probe.target_ids,
            )
            seeded_group_probe["selector_score"] = round(selector_score, 6)
            seeded_group_probe["selector_diagnostics"] = selector_diag
            break
        target_groups = [
            group for group in groups
            if set(group.member_ids) & set(probe.target_ids)
        ]
        if target_groups:
            best_target_group = max(target_groups, key=lambda group: group.score_measurement)
            if len(best_target_group.member_ids) >= 3:
                target_group_probe = _local_group_probe(
                    signal,
                    ids,
                    matrix,
                    best_target_group,
                    support_limit=support_limit,
                    target_ids=probe.target_ids,
                )

    residual = _residual_concentration(signal, ids, matrix, omp["support_ids"], groups=groups)
    pool_bounds = _candidate_pool_upper_bounds(candidates, probe, groups=groups)

    return {
        "probe_id": probe.probe_id,
        "family": probe.family,
        "mode": probe.mode,
        "target_count": len(probe.target_ids),
        "candidate_count": len(candidates),
        "flat_metrics": flat["metrics"],
        "grouped_metrics": grouped_metrics,
        "omp_metrics": omp["metrics"],
        "omp_residual_ratio": omp.get("residual_ratio", 1.0),
        "pool_spectral": pool_stats,
        "rep_steps": rep_steps,
        "residual_concentration": residual,
        "pool_bounds": pool_bounds,
        "top_group_probe": top_group_probe,
        "selected_group_probe": selected_group_probe,
        "seeded_group_probe": seeded_group_probe,
        "target_group_probe": target_group_probe,
        "group_summary": {
            kind: {
                "n_groups": len(rows),
                "effective_rank_mean": _mean([row["effective_rank"] for row in rows]),
                "pc1_explained_mean": _mean([row["pc1_explained"] for row in rows]),
                "pc2_explained_mean": _mean([row["pc2_explained"] for row in rows]),
                "rep2_mean_nearest": _mean([row.get("rep2_mean_nearest", 0.0) for row in rows]),
            }
            for kind, rows in group_summary.items()
        },
    }


def _summarize_probe_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped_rows = [row for row in rows if row.get("grouped_metrics") is not None]
    selected_group_rows = [row for row in rows if row.get("selected_group_probe") is not None]
    seeded_group_rows = [row for row in rows if row.get("seeded_group_probe") is not None]
    target_group_rows = [row for row in rows if row.get("target_group_probe") is not None]
    top_group_rows = [row for row in rows if row.get("top_group_probe") is not None]
    return {
        "n": len(rows),
        "flat_strict_hit_rate": _mean([1.0 if row["flat_metrics"]["strict_hit"] else 0.0 for row in rows]),
        "flat_strict_recall_mean": _mean([row["flat_metrics"]["strict_recall"] for row in rows]),
        "grouped_strict_hit_rate": _mean([
            1.0 if row["grouped_metrics"]["strict_hit"] else 0.0 for row in grouped_rows
        ]),
        "grouped_strict_recall_mean": _mean([
            row["grouped_metrics"]["strict_recall"] for row in grouped_rows
        ]),
        "omp_strict_hit_rate": _mean([1.0 if row["omp_metrics"]["strict_hit"] else 0.0 for row in rows]),
        "omp_strict_recall_mean": _mean([row["omp_metrics"]["strict_recall"] for row in rows]),
        "omp_residual_ratio_mean": _mean([row["omp_residual_ratio"] for row in rows]),
        "pool_effective_rank_mean": _mean([row["pool_spectral"]["effective_rank"] for row in rows]),
        "pool_pc1_explained_mean": _mean([row["pool_spectral"]["pc1_explained"] for row in rows]),
        "rep3_mean_nearest_mean": _mean([
            row["rep_steps"][min(2, len(row["rep_steps"]) - 1)]["mean_nearest_cosine"]
            for row in rows
            if row["rep_steps"]
        ]),
        "base_residual_top1_share_mean": _mean([
            row["residual_concentration"].get("base", {}).get("top1_share", 0.0)
            for row in rows
        ]),
        "base_residual_entropy_mean": _mean([
            row["residual_concentration"].get("base", {}).get("entropy", 0.0)
            for row in rows
        ]),
        "top_group_omp_hit_rate": _mean([
            1.0 if (row.get("top_group_probe") or {}).get("omp_metrics", {}).get("strict_hit") else 0.0
            for row in top_group_rows
        ]),
        "top_group_omp_recall_mean": _mean([
            (row.get("top_group_probe") or {}).get("omp_metrics", {}).get("strict_recall", 0.0)
            for row in top_group_rows
        ]),
        "selected_group_omp_hit_rate": _mean([
            1.0 if (row.get("selected_group_probe") or {}).get("omp_metrics", {}).get("strict_hit") else 0.0
            for row in selected_group_rows
        ]),
        "selected_group_omp_recall_mean": _mean([
            (row.get("selected_group_probe") or {}).get("omp_metrics", {}).get("strict_recall", 0.0)
            for row in selected_group_rows
        ]),
        "selected_group_pc1_mean": _mean([
            (row.get("selected_group_probe") or {}).get("spectral", {}).get("pc1_explained", 0.0)
            for row in selected_group_rows
        ]),
        "seeded_group_omp_hit_rate": _mean([
            1.0 if (row.get("seeded_group_probe") or {}).get("omp_metrics", {}).get("strict_hit") else 0.0
            for row in seeded_group_rows
        ]),
        "seeded_group_omp_recall_mean": _mean([
            (row.get("seeded_group_probe") or {}).get("omp_metrics", {}).get("strict_recall", 0.0)
            for row in seeded_group_rows
        ]),
        "seeded_group_pc1_mean": _mean([
            (row.get("seeded_group_probe") or {}).get("spectral", {}).get("pc1_explained", 0.0)
            for row in seeded_group_rows
        ]),
        "target_group_omp_hit_rate": _mean([
            1.0 if (row.get("target_group_probe") or {}).get("omp_metrics", {}).get("strict_hit") else 0.0
            for row in target_group_rows
        ]),
        "target_group_omp_recall_mean": _mean([
            (row.get("target_group_probe") or {}).get("omp_metrics", {}).get("strict_recall", 0.0)
            for row in target_group_rows
        ]),
        "target_group_pc1_mean": _mean([
            (row.get("target_group_probe") or {}).get("spectral", {}).get("pc1_explained", 0.0)
            for row in target_group_rows
        ]),
        "pool_strict_hit_rate": _mean([
            1.0 if row["pool_bounds"]["pool_strict_hit"] else 0.0 for row in rows
        ]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=str, required=True)
    parser.add_argument("--families", nargs="+", default=["version", "edge"])
    parser.add_argument("--probes-per-family", type=int, default=20)
    parser.add_argument("--qa-dataset", type=str, default=None)
    parser.add_argument("--qa-mode", type=str, choices=["auto", "around", "towards"], default="towards")
    parser.add_argument("--query-note-model", action="store_true", default=False)
    parser.add_argument("--semantic-limit", type=int, default=30)
    parser.add_argument("--fts-limit", type=int, default=30)
    parser.add_argument("--seed-limit", type=int, default=8)
    parser.add_argument("--edge-limit", type=int, default=6)
    parser.add_argument("--version-limit", type=int, default=4)
    parser.add_argument("--part-limit", type=int, default=6)
    parser.add_argument("--support-limit", type=int, default=6)
    parser.add_argument("--version-lineages", type=int, default=60)
    parser.add_argument("--version-min-count", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    store_path = Path(args.store).expanduser().resolve()
    qa_dataset = Path(args.qa_dataset).expanduser().resolve() if args.qa_dataset else None

    kp = Keeper(store_path=store_path)
    started_at = time.perf_counter()
    probes = _load_probes(
        kp,
        rng,
        families=list(args.families),
        probes_per_family=args.probes_per_family,
        qa_dataset=qa_dataset,
        qa_mode=args.qa_mode,
    )

    rows: list[dict[str, Any]] = []
    for probe in probes:
        rows.append(_probe_experiment(
            kp,
            probe,
            query_note_model=args.query_note_model,
            semantic_limit=args.semantic_limit,
            fts_limit=args.fts_limit,
            seed_limit=args.seed_limit,
            edge_limit=args.edge_limit,
            version_limit=args.version_limit,
            part_limit=args.part_limit,
            support_limit=args.support_limit,
        ))

    version_delta = _version_delta_stats(
        kp,
        rng,
        sample_size=args.version_lineages,
        min_versions=args.version_min_count,
    )
    kp.close()

    by_family_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = f"{row['family']}:{row['mode']}"
        by_family_mode[key].append(row)

    summary = {
        "store": str(store_path),
        "probe_count": len(rows),
        "runtime_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "overall": _summarize_probe_rows(rows),
        "by_family_mode": {
            key: _summarize_probe_rows(bucket)
            for key, bucket in sorted(by_family_mode.items())
        },
        "version_delta": version_delta,
    }

    payload = {
        "summary": summary,
        "rows": rows,
    }

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {out_path}")

    print("Summary")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
