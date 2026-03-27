#!/usr/bin/env python3
"""Benchmark get_context component timing.

Runs against a real store (default: ~/.keep) to measure wall-clock
time for each component of context assembly:

  - item lookup
  - similar (vector search)
  - meta (meta-doc resolution)
  - edges (edge tag traversal)
  - parts (part listing)
  - versions (version navigation)
  - total (full get_context)

Usage:
    python bench/context_perf.py                  # default store, "now" item
    python bench/context_perf.py --id file:///x    # specific item
    python bench/context_perf.py --runs 20         # more iterations
    python bench/context_perf.py --store /tmp/s    # custom store

Results are printed to stdout as a table. Not committed — run locally
before and after cache changes to compare.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Benchmark get_context components")
    parser.add_argument("--store", type=str, default=None, help="Store path")
    parser.add_argument("--id", type=str, default="now", help="Item ID to benchmark")
    parser.add_argument("--runs", type=int, default=10, help="Number of iterations")
    parser.add_argument("--warmup", type=int, default=2, help="Warmup iterations (excluded)")
    parser.add_argument("--similar-limit", type=int, default=3)
    parser.add_argument("--meta-limit", type=int, default=3)
    parser.add_argument("--edges-limit", type=int, default=5)
    parser.add_argument("--parts-limit", type=int, default=10)
    parser.add_argument("--versions-limit", type=int, default=3)
    args = parser.parse_args()

    from keep.api import Keeper

    store_path = Path(args.store) if args.store else None
    kp = Keeper(store_path=store_path)

    # Verify item exists
    item = kp.get(args.id)
    if item is None:
        if args.id == "now":
            kp.get_now()
        else:
            print(f"Item not found: {args.id}", file=sys.stderr)
            sys.exit(1)

    limits = {
        "similar_limit": args.similar_limit,
        "meta_limit": args.meta_limit,
        "edges_limit": args.edges_limit,
        "parts_limit": args.parts_limit,
        "versions_limit": args.versions_limit,
    }

    # Component-level benchmarks: call get_context with only one
    # component enabled at a time to isolate costs.
    components = {
        "similar": {"include_similar": True, "include_meta": False, "include_parts": False, "include_versions": False},
        "meta": {"include_similar": False, "include_meta": True, "include_parts": False, "include_versions": False},
        "edges": {"include_similar": False, "include_meta": False, "include_parts": False, "include_versions": False},
        "parts": {"include_similar": False, "include_meta": False, "include_parts": True, "include_versions": False},
        "versions": {"include_similar": False, "include_meta": False, "include_parts": False, "include_versions": True},
        "full": {"include_similar": True, "include_meta": True, "include_parts": True, "include_versions": True},
    }

    # Note: "edges" are always resolved when the flow runs (they're in the
    # get state doc). The edges-only benchmark disables similar/meta/parts
    # but edges still come through the flow.

    results: dict[str, list[float]] = {name: [] for name in components}

    total_runs = args.warmup + args.runs
    print(f"Benchmarking get_context({args.id!r}), {args.runs} runs + {args.warmup} warmup\n", file=sys.stderr)

    for i in range(total_runs):
        is_warmup = i < args.warmup
        for name, flags in components.items():
            t0 = time.perf_counter()
            ctx = kp.get_context(args.id, **limits, **flags)
            elapsed = time.perf_counter() - t0
            if not is_warmup:
                results[name].append(elapsed * 1000)  # ms
            if ctx is None:
                print(f"Warning: get_context returned None for {name}", file=sys.stderr)

    # Also benchmark raw item lookup (no context)
    raw_times = []
    for i in range(args.warmup + args.runs):
        t0 = time.perf_counter()
        kp.get(args.id)
        elapsed = time.perf_counter() - t0
        if i >= args.warmup:
            raw_times.append(elapsed * 1000)

    kp.close()

    # Report
    print(f"{'Component':<12} {'Mean':>8} {'P50':>8} {'P95':>8} {'Min':>8} {'Max':>8}  (ms, n={args.runs})")
    print("-" * 68)

    def report(name: str, times: list[float]):
        if not times:
            print(f"{name:<12} {'N/A':>8}")
            return
        s = sorted(times)
        mean = statistics.mean(s)
        p50 = s[len(s) // 2]
        p95 = s[int(len(s) * 0.95)]
        mn = s[0]
        mx = s[-1]
        print(f"{name:<12} {mean:8.1f} {p50:8.1f} {p95:8.1f} {mn:8.1f} {mx:8.1f}")

    report("item", raw_times)
    for name in ["similar", "meta", "edges", "parts", "versions", "full"]:
        report(name, results[name])


if __name__ == "__main__":
    main()
