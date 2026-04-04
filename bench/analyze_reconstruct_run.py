#!/usr/bin/env python3
"""Summarize reconstruction spike bounds and timing diagnostics.

This script answers the next questions raised by the spike:

- Are strict/base misses caused by the bounded candidate pool?
- If not, did grouping fail to induce the right neighborhood?
- If not, did representative emission drop the target?
- Which parts of candidate-pool construction dominate runtime?

It reads the JSON payload emitted by ``bench/reconstruct_spike.py`` and
prints aggregate counts over the selected methods.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_METHODS = ["reconstruct", "reconstruct_multi_towards", "reconstruct_grouped", "deep", "fts", "fused", "semantic"]


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _classify_strict_miss(record: dict[str, Any], method_block: dict[str, Any], pool_bounds: dict[str, Any]) -> str:
    probe = record.get("probe", {})
    if not probe.get("target_ids"):
        return "no_targets"
    metrics = method_block.get("metrics", {})
    if metrics.get("strict_hit"):
        return "hit"
    if not pool_bounds.get("pool_strict_hit"):
        return "missing_from_pool"

    group_bounds = pool_bounds.get("group_upper_bounds") or {}
    if group_bounds:
        if not group_bounds.get("group_strict_hit"):
            return "missing_from_groups"
        if not group_bounds.get("emit_strict_hit"):
            return "missing_from_emission"
        return "selection_miss"
    return "selection_miss"


def _classify_base_miss(record: dict[str, Any], method_block: dict[str, Any], pool_bounds: dict[str, Any]) -> str:
    probe = record.get("probe", {})
    if not probe.get("target_ids"):
        return "no_targets"
    metrics = method_block.get("metrics", {})
    if metrics.get("base_hit"):
        return "hit"
    if not pool_bounds.get("pool_base_hit"):
        return "missing_from_pool"

    group_bounds = pool_bounds.get("group_upper_bounds") or {}
    if group_bounds:
        if not group_bounds.get("group_base_hit"):
            return "missing_from_groups"
        if not group_bounds.get("emit_base_hit"):
            return "missing_from_emission"
        return "selection_miss"
    return "selection_miss"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_json", help="Path to reconstruct_spike JSON output")
    parser.add_argument("--methods", nargs="+", default=DEFAULT_METHODS,
                        help="Methods to summarize (default: common retrieval/reconstruction methods)")
    args = parser.parse_args()

    payload = _load(Path(args.run_json).expanduser().resolve())
    records = list(payload.get("records", []))
    if not records:
        print("No records found.")
        return 1

    method_names = [name for name in args.methods]
    strict_classes: dict[str, Counter[str]] = {name: Counter() for name in method_names}
    base_classes: dict[str, Counter[str]] = {name: Counter() for name in method_names}
    method_runtime: dict[str, list[float]] = defaultdict(list)
    candidate_pool_runtime: list[float] = []
    timing_breakdown: dict[str, list[float]] = defaultdict(list)
    counter_breakdown: dict[str, list[int]] = defaultdict(list)
    deep_only_strict_missing: list[int] = []
    deep_only_base_missing: list[int] = []

    for record in records:
        methods = record.get("methods", {})
        pool = methods.get("candidate_pool", {})
        pool_bounds = pool.get("upper_bounds", {})
        candidate_pool_runtime.append(float(pool.get("runtime_ms", 0.0) or 0.0))
        for key, value in (pool.get("timings_ms") or {}).items():
            timing_breakdown[key].append(float(value or 0.0))
        for key, value in (pool.get("counters") or {}).items():
            counter_breakdown[key].append(int(value or 0))

        deep_only = pool_bounds.get("deep_only") or {}
        deep_only_strict_missing.append(len(deep_only.get("strict_missing_from_pool") or []))
        deep_only_base_missing.append(len(deep_only.get("base_missing_from_pool") or []))

        for name in method_names:
            method_block = methods.get(name)
            if not method_block:
                continue
            method_runtime[name].append(float(method_block.get("runtime_ms", 0.0) or 0.0))
            strict_classes[name][_classify_strict_miss(record, method_block, pool_bounds)] += 1
            base_classes[name][_classify_base_miss(record, method_block, pool_bounds)] += 1

    print("Run")
    print(f"Records: {len(records)}")
    print()

    print("Pool Bounds")
    print(f"Candidate pool runtime mean: {_mean(candidate_pool_runtime):.2f} ms")
    print(f"Deep-only strict missing mean: {_mean(deep_only_strict_missing):.2f}")
    print(f"Deep-only base missing mean: {_mean(deep_only_base_missing):.2f}")
    print()

    print("Pool Timing Breakdown")
    for key, values in sorted(timing_breakdown.items(), key=lambda item: _mean(item[1]), reverse=True):
        print(f"{key:24s} mean={_mean(values):8.2f} ms")
    print()

    print("Pool Counters")
    for key, values in sorted(counter_breakdown.items()):
        print(f"{key:24s} mean={_mean([float(v) for v in values]):8.2f}")
    print()

    print("Strict Miss Classification")
    for name in method_names:
        counts = strict_classes[name]
        if not counts:
            continue
        print(
            f"{name:20s} hit={counts['hit']:3d} "
            f"none={counts['no_targets']:3d} "
            f"pool={counts['missing_from_pool']:3d} "
            f"group={counts['missing_from_groups']:3d} "
            f"emit={counts['missing_from_emission']:3d} "
            f"select={counts['selection_miss']:3d}"
        )
    print()

    print("Base Miss Classification")
    for name in method_names:
        counts = base_classes[name]
        if not counts:
            continue
        print(
            f"{name:20s} hit={counts['hit']:3d} "
            f"none={counts['no_targets']:3d} "
            f"pool={counts['missing_from_pool']:3d} "
            f"group={counts['missing_from_groups']:3d} "
            f"emit={counts['missing_from_emission']:3d} "
            f"select={counts['selection_miss']:3d}"
        )
    print()

    print("Method Runtime")
    for name in method_names:
        values = method_runtime.get(name)
        if not values:
            continue
        print(f"{name:20s} mean={_mean(values):8.2f} ms")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
