#!/usr/bin/env python3
"""
Test sliding-window analysis with different providers on real data.

Providers:
  --provider ollama      Local Ollama model (default)
  --provider anthropic   Anthropic Claude API
  --provider openai      OpenAI API
  --provider gemini      Google Gemini API

Data sources:
  --source now       Version history of 'now' (default)
  --source longmem   LongMemEval oracle conversations
  --source file      A file from disk (split into paragraph chunks)

Usage:
    python tests/test_sliding_window.py                           # ollama, temporal-v2
    python tests/test_sliding_window.py --provider anthropic      # claude-haiku-4-5
    python tests/test_sliding_window.py --provider openai --model gpt-5-mini
    python tests/test_sliding_window.py --provider gemini         # gemini-2.5-flash
    python tests/test_sliding_window.py --prompt temporal-v3      # different prompt
    python tests/test_sliding_window.py --source longmem          # longmem data
    python tests/test_sliding_window.py --source longmem --item 3 # specific item
    python tests/test_sliding_window.py --source file --file docs/library/han_verse.txt
    python tests/test_sliding_window.py --budget 2000             # tight windows
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from keep.providers.base import AnalysisChunk
from keep.analyzers import SlidingWindowAnalyzer, PROMPTS, get_budget_for_model

LONGMEM_DATA = Path(__file__).parent.parent.parent / "keepmem" / "bench" / "longmemeval" / "data" / "longmemeval_oracle.json"

# Default models per provider
DEFAULT_MODELS = {
    "ollama": "llama3.2:3b",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-5-mini",
    "gemini": "gemini-2.5-flash",
}


def make_provider(provider_name, model):
    """Instantiate a summarization provider by name."""
    if provider_name == "ollama":
        from keep.providers.llm import OllamaSummarization
        return OllamaSummarization(model=model)
    elif provider_name == "anthropic":
        from keep.providers.llm import AnthropicSummarization
        return AnthropicSummarization(model=model)
    elif provider_name == "openai":
        from keep.providers.llm import OpenAISummarization
        return OpenAISummarization(model=model)
    elif provider_name == "gemini":
        from keep.providers.llm import GeminiSummarization
        return GeminiSummarization(model=model)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


def get_now_chunks(store_path=None, limit=10):
    """Get recent versions of 'now' as AnalysisChunks."""
    from keep.api import Keeper

    kp = Keeper(store_path=store_path)
    doc_coll = kp._resolve_doc_collection()

    doc_record = kp._document_store.get(doc_coll, "now")
    if doc_record is None:
        kp.close()
        return []

    versions = kp._document_store.list_versions(doc_coll, "now", limit=limit)
    kp.close()

    if not versions:
        return [AnalysisChunk(content=doc_record.summary, tags={}, index=0)]

    chunks = []
    for i, v in enumerate(reversed(versions)):
        date_str = v.created_at[:10] if v.created_at else ""
        chunks.append(AnalysisChunk(
            content=f"[{date_str}]\n{v.summary}",
            tags={},
            index=i,
        ))
    chunks.append(AnalysisChunk(
        content=f"[current]\n{doc_record.summary}",
        tags={},
        index=len(chunks),
    ))
    return chunks


def get_longmem_chunks(item_index=0):
    """Get a LongMemEval conversation as AnalysisChunks."""
    data = json.loads(LONGMEM_DATA.read_text())
    if item_index >= len(data):
        print(f"Item index {item_index} out of range (max {len(data) - 1})")
        return [], {}

    item = data[item_index]
    print(f"Question [{item['question_id']}]: {item['question']}")
    print(f"Answer: {item['answer']}")
    print(f"Type: {item['question_type']}")
    print(f"Sessions: {len(item['haystack_sessions'])}")

    # Build chunks: one per conversation turn, grouped by session
    chunks = []
    idx = 0
    for sess_i, session in enumerate(item['haystack_sessions']):
        for turn in session:
            role = turn['role']
            content = turn['content']
            label = f"[Session {sess_i + 1}, {role}]"
            chunks.append(AnalysisChunk(
                content=f"{label}\n{content}",
                tags={"has_answer": str(turn.get("has_answer", False))},
                index=idx,
            ))
            idx += 1

    return chunks, item


def get_file_chunks(file_path):
    """Split a file into paragraph-level AnalysisChunks."""
    path = Path(file_path)
    if not path.exists():
        print(f"File not found: {path}")
        return []

    text = path.read_text(errors="replace")
    print(f"File: {path.name} ({len(text)} chars)")

    # Strip HTML tags if needed
    if path.suffix == ".html":
        import re
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)

    if path.suffix == ".json":
        import json as json_mod
        try:
            data = json_mod.loads(text)
            if isinstance(data, dict):
                segments = [f"{k}: {v}" for k, v in data.items() if isinstance(v, str) and len(v) > 20]
                return [AnalysisChunk(content=s, tags={}, index=i) for i, s in enumerate(segments)]
        except json_mod.JSONDecodeError:
            pass

    # Split into paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    # Merge very short paragraphs with next one
    merged = []
    buf = ""
    for p in paragraphs:
        buf = f"{buf}\n\n{p}" if buf else p
        if len(buf) > 200:
            merged.append(buf)
            buf = ""
    if buf:
        merged.append(buf)

    return [AnalysisChunk(content=p, tags={}, index=i) for i, p in enumerate(merged)]


def run_analysis(chunks, prompt_name, provider_name, model, budget):
    """Run sliding-window analysis and display results."""
    provider = make_provider(provider_name, model)

    analyzer = SlidingWindowAnalyzer(
        provider=provider,
        context_budget=budget,
        prompt=prompt_name,
    )

    print(f"\nRunning analysis ({prompt_name})...")
    t0 = time.time()
    parts = analyzer.analyze(chunks)
    elapsed = time.time() - t0

    print(f"\nCompleted in {elapsed:.1f}s")
    print(f"Found {len(parts)} parts")
    print("=" * 70)

    for i, part in enumerate(parts):
        print(f"\n[{i + 1}] {part['summary']}")

    print(f"\n{'=' * 70}")
    print(f"{len(parts)} parts from {len(chunks)} chunks in {elapsed:.1f}s")
    print(f"Provider: {provider_name}, Model: {model}, Prompt: {prompt_name}, Budget: {budget}")
    return parts


def main():
    prompt_names = list(PROMPTS.keys())
    provider_names = list(DEFAULT_MODELS.keys())

    parser = argparse.ArgumentParser(description="Test sliding-window analysis")
    parser.add_argument("--provider", choices=provider_names, default="ollama",
                        help="LLM provider (default: ollama)")
    parser.add_argument("--prompt", choices=prompt_names, default="temporal-v2")
    parser.add_argument("--source", choices=["now", "longmem", "file"], default="now")
    parser.add_argument("--versions", type=int, default=10, help="Versions of 'now' (default: 10)")
    parser.add_argument("--item", type=int, default=0, help="LongMem item index (default: 0)")
    parser.add_argument("--file", default=None, help="File path for --source file")
    parser.add_argument("--model", default=None, help="Model name (default: per provider)")
    parser.add_argument("--budget", type=int, default=None,
                        help="Context budget in tokens (default: auto per model)")
    parser.add_argument("--store", default=None)
    args = parser.parse_args()

    model = args.model or DEFAULT_MODELS[args.provider]
    budget = args.budget or get_budget_for_model(model, args.provider)

    print(f"Provider: {args.provider}")
    print(f"Model: {model}")
    print(f"Prompt: {args.prompt}")
    print(f"Context budget: {budget} tokens")
    print(f"Source: {args.source}")
    print("=" * 70)

    if args.source == "now":
        print(f"Fetching last {args.versions} versions of 'now'...")
        chunks = get_now_chunks(store_path=args.store, limit=args.versions)
    elif args.source == "longmem":
        print(f"Loading LongMem item {args.item}...")
        chunks, item = get_longmem_chunks(args.item)
    elif args.source == "file":
        if not args.file:
            print("--file required with --source file")
            return
        chunks = get_file_chunks(args.file)

    if not chunks:
        print("No data found.")
        return

    print(f"\nGot {len(chunks)} chunks")
    for i, c in enumerate(chunks):
        preview = c.content[:100].replace("\n", " ")
        print(f"  [{i}] ({len(c.content)} chars) {preview}...")

    total_chars = sum(len(c.content) for c in chunks)
    print(f"\nTotal: {total_chars} chars (~{total_chars // 4} tokens)")
    print("=" * 70)

    run_analysis(chunks, args.prompt, args.provider, model, budget)


if __name__ == "__main__":
    main()
