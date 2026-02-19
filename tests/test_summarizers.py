#!/usr/bin/env python3
"""
Compare summarizer performance and quality: truncate, first-paragraph, Ollama, MLX.

Usage:
    python tests/test_summarizers.py              # Run all available
    python tests/test_summarizers.py --ollama     # Ollama only (run first)
    python tests/test_summarizers.py --mlx        # MLX only (run after stopping Ollama)
"""
import argparse
import resource
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "docs" / "library"
test_file = DATA_DIR / "fortytwo_chapters.txt"
content = test_file.read_text()[:3000]


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def run_test(label, provider, content, runs=3):
    """Run summarization, report timing and quality."""
    print(f"\n{label}")
    print("-" * 70)
    print(f"RSS before: {rss_mb():.0f} MB")

    # Warm-up run (model loading, JIT, etc.)
    print("Warm-up...", end=" ", flush=True)
    t0 = time.time()
    warmup = provider.summarize(content, max_length=500)
    warmup_time = time.time() - t0
    print(f"{warmup_time:.1f}s")

    # Timed runs
    times = []
    last_summary = warmup
    for i in range(runs):
        t0 = time.time()
        last_summary = provider.summarize(content, max_length=500)
        elapsed = time.time() - t0
        times.append(elapsed)
        print(f"  Run {i+1}: {elapsed:.2f}s")

    avg = sum(times) / len(times)
    print(f"Average: {avg:.2f}s  (warmup: {warmup_time:.2f}s)")
    print(f"Length: {len(last_summary)} chars")
    print(f"RSS after: {rss_mb():.0f} MB")
    print(f"Summary:\n{last_summary}")
    return avg, last_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama", action="store_true", help="Ollama only")
    parser.add_argument("--mlx", action="store_true", help="MLX only")
    args = parser.parse_args()

    run_all = not args.ollama and not args.mlx

    print(f"Testing with {len(content)} chars from {test_file.name}")
    print(f"RSS baseline: {rss_mb():.0f} MB")
    print("=" * 70)

    results = {}

    # --- Baseline: truncate + first-paragraph ---
    if run_all:
        from keep.providers.summarization import TruncationSummarizer, FirstParagraphSummarizer

        trunc = TruncationSummarizer(max_length=500)
        t0 = time.time()
        trunc_summary = trunc.summarize(content)
        trunc_time = time.time() - t0
        print(f"\nTRUNCATE: {trunc_time*1000:.1f}ms, {len(trunc_summary)} chars")
        print(f"  {trunc_summary[:120]}...")
        results["truncate"] = trunc_time

        fp = FirstParagraphSummarizer(max_length=500)
        t0 = time.time()
        fp_summary = fp.summarize(content)
        fp_time = time.time() - t0
        print(f"\nFIRST_PARAGRAPH: {fp_time*1000:.1f}ms, {len(fp_summary)} chars")
        print(f"  {fp_summary[:120]}...")
        results["first_para"] = fp_time

    # --- Ollama ---
    if run_all or args.ollama:
        from keep.providers.llm import OllamaSummarization

        # Test with llama3.2:3b (same family as MLX Llama-3.2-3B)
        print("\n" + "=" * 70)
        try:
            ollama_llama = OllamaSummarization(model="llama3.2:3b")
            avg, _ = run_test("OLLAMA llama3.2:3b", ollama_llama, content)
            results["ollama_llama3.2"] = avg
        except Exception as e:
            print(f"Ollama llama3.2:3b failed: {e}")

        # Test with gemma3:1b (smaller, faster)
        try:
            ollama_gemma = OllamaSummarization(model="gemma3:1b")
            avg, _ = run_test("OLLAMA gemma3:1b", ollama_gemma, content)
            results["ollama_gemma3:1b"] = avg
        except Exception as e:
            print(f"Ollama gemma3:1b failed: {e}")

    # --- MLX ---
    if run_all or args.mlx:
        print("\n" + "=" * 70)
        try:
            from keep.providers.mlx import MLXSummarization
            mlx = MLXSummarization(model="mlx-community/Llama-3.2-3B-Instruct-4bit")
            avg, _ = run_test("MLX Llama-3.2-3B-Instruct-4bit", mlx, content)
            results["mlx_llama3.2"] = avg
        except (ImportError, RuntimeError) as e:
            print(f"MLX not available: {e}")

    # --- Summary ---
    if len(results) > 1:
        print("\n" + "=" * 70)
        print("COMPARISON")
        print("-" * 70)
        for name, t in sorted(results.items(), key=lambda x: x[1]):
            print(f"  {name:25s} {t*1000:8.1f} ms")

    print(f"\nFinal RSS: {rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
