#!/usr/bin/env python3
"""
Investigation script: temporal decomposition prompts with MLX.

Tests different analysis prompts against real version-history data from keep.
Loads the MLX model, runs ONE inference, prints results, exits.
Run multiple times with different --prompt to compare.

Usage:
    # Current structural prompt (baseline)
    python scripts/investigate_prompts.py --prompt structural --item save1

    # Proposed temporal/episodic prompt
    python scripts/investigate_prompts.py --prompt temporal --item save1

    # With a specific budget (chars, rough proxy for tokens)
    python scripts/investigate_prompts.py --prompt temporal --item now --budget 4000

    # Use the 1B model (faster, less capable)
    python scripts/investigate_prompts.py --prompt temporal --model mlx-community/Llama-3.2-1B-Instruct-4bit

    # Dry run (print prompt only, no MLX)
    python scripts/investigate_prompts.py --prompt temporal --item save1 --dry-run
"""

import argparse
import sys
import time


# ---------------------------------------------------------------------------
# Prompts to compare
# ---------------------------------------------------------------------------

PROMPT_STRUCTURAL = {
    "name": "structural (current)",
    "system": """You are a document analysis assistant. You will receive a conversation thread wrapped in <content> tags. A portion is marked with <analyze> tags — decompose ONLY that portion into meaningful parts.

Write one summary per line. Each summary should be 1-2 sentences capturing a coherent unit of meaning (a topic, decision, or exchange).

Rules:
- Only summarize content inside <analyze> tags
- One summary per line, no numbering, no bullet points
- If nothing is noteworthy, write: EMPTY""",
}

PROMPT_TEMPORAL = {
    "name": "temporal/episodic",
    "system": """You are analyzing the evolution of a conversation over time. You will receive dated entries wrapped in <content> tags. A portion is marked with <analyze> tags — analyze ONLY that portion.

For each significant development, write one line capturing WHAT CHANGED and WHY IT MATTERS. Focus on:
- Decisions made or reversed ("Chose X over Y because...")
- Commitments given or fulfilled ("Committed to X" / "Delivered X")
- Breakdowns — where assumptions were revealed ("Expected X but found Y")
- Themes that persist across entries ("Authentication remains the focus")
- Turning points where direction shifted

Write one observation per line. Each should name the change and its significance.

Rules:
- Only analyze content inside <analyze> tags
- Use surrounding content for context but don't summarize it
- One observation per line, no numbering, no bullet points
- Prefer "X changed to Y" over "X was discussed"
- If nothing is noteworthy, write: EMPTY""",
}

PROMPT_COMMITMENTS = {
    "name": "commitment-tracking",
    "system": """You are analyzing a conversation for speech acts and commitments. You will receive dated entries wrapped in <content> tags. A portion is marked with <analyze> tags — analyze ONLY that portion.

Identify each speech act — requests, promises, assertions, declarations — and track their lifecycle:
- OPEN: commitment made but not yet fulfilled
- FULFILLED: promise kept, request satisfied
- WITHDRAWN: commitment explicitly abandoned
- BROKEN: promise not kept (breakdown)

Write one line per speech act found. Format: "[status] Actor committed/requested/declared: description"

Rules:
- Only analyze content inside <analyze> tags
- One speech act per line, no numbering
- Skip procedural exchanges (greetings, acknowledgments)
- If no speech acts found, write: EMPTY""",
}

PROMPT_TEMPORAL_V2 = {
    "name": "temporal-v2 (concise, no echo)",
    "system": """Analyze the evolution of a conversation. Entries are dated and wrapped in <content> tags. Only analyze content inside <analyze> tags.

Write ONE LINE per significant development. DO NOT repeat or echo the original text. Synthesize.

Good: "Decision reversed: mtime dropped in favor of birthtime for created_at"
Bad: "User prompt: Actually no. We updated the interface..."

Focus on: decisions, reversals, commitments fulfilled, breakdowns, persistent themes, turning points.

Rules:
- One observation per line, no numbering, no bullets
- Never copy input text — always synthesize in your own words
- Skip routine exchanges (greetings, task notifications)
- If nothing noteworthy: EMPTY""",
}

PROMPTS = {
    "structural": PROMPT_STRUCTURAL,
    "temporal": PROMPT_TEMPORAL,
    "temporal-v2": PROMPT_TEMPORAL_V2,
    "commitments": PROMPT_COMMITMENTS,
}


# ---------------------------------------------------------------------------
# Build chunks from keep version history
# ---------------------------------------------------------------------------

def build_chunks(item_id: str):
    """Load version history from keep and build analysis chunks."""
    from keep.api import Keeper

    kp = Keeper()
    doc = kp.get(item_id)
    if not doc:
        print(f"Item '{item_id}' not found", file=sys.stderr)
        sys.exit(1)

    versions = kp.list_versions(item_id)
    chunks = []

    # Chronological (oldest first) — matches api.py:2886-2901
    for i, v in enumerate(reversed(versions)):
        date_str = v.created_at[:10] if v.created_at else ""
        chunks.append({
            "content": f"[{date_str}]\n{v.summary}",
            "index": i,
        })

    # Current version as newest
    chunks.append({
        "content": f"[current]\n{doc.summary}",
        "index": len(chunks),
    })

    return chunks


def build_window_prompt(chunks, target_start, target_end):
    """Build XML-tagged prompt matching SlidingWindowAnalyzer._build_window_prompt."""
    parts = ["<content>"]
    for i, chunk in enumerate(chunks):
        if i == target_start:
            parts.append("<analyze>")
        parts.append(chunk["content"])
        if i == target_end - 1:
            parts.append("</analyze>")
    parts.append("</content>")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Test analysis prompts with MLX")
    parser.add_argument(
        "--prompt", choices=list(PROMPTS.keys()), default="temporal",
        help="Which prompt to test (default: temporal)",
    )
    parser.add_argument(
        "--item", default="save1",
        help="Keep item ID to use as test data (default: save1)",
    )
    parser.add_argument(
        "--model", default="mlx-community/Llama-3.2-3B-Instruct-4bit",
        help="MLX model to use",
    )
    parser.add_argument(
        "--budget", type=int, default=6000,
        help="Rough char budget for the window (default: 6000)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=1024,
        help="Max tokens to generate (default: 1024)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the prompt without running MLX",
    )
    args = parser.parse_args()

    prompt_config = PROMPTS[args.prompt]
    print(f"=== Prompt: {prompt_config['name']} ===")
    print(f"=== Item: {args.item} ===")
    print()

    # Build chunks from version history
    chunks = build_chunks(args.item)
    print(f"Loaded {len(chunks)} chunks ({sum(len(c['content']) for c in chunks)} chars total)")

    # For this investigation, send everything as one window (all chunks are targets)
    # If content exceeds budget, we truncate from the oldest end
    total_chars = sum(len(c["content"]) for c in chunks)
    if total_chars > args.budget:
        # Keep the newest chunks that fit within budget
        kept = []
        running = 0
        for chunk in reversed(chunks):
            if running + len(chunk["content"]) > args.budget and kept:
                break
            kept.append(chunk)
            running += len(chunk["content"])
        chunks = list(reversed(kept))
        # Re-index
        for i, c in enumerate(chunks):
            c["index"] = i
        print(f"Trimmed to {len(chunks)} chunks ({sum(len(c['content']) for c in chunks)} chars) to fit budget")

    # Build the prompt (all chunks are analyze targets)
    user_prompt = build_window_prompt(chunks, 0, len(chunks))

    print()
    print(f"--- System prompt ({len(prompt_config['system'])} chars) ---")
    print(prompt_config["system"])
    print()
    print(f"--- User prompt ({len(user_prompt)} chars) ---")
    print(user_prompt)
    print()

    if args.dry_run:
        print("(dry run — skipping MLX inference)")
        return

    # Load model and run single inference
    print(f"Loading {args.model}...")
    t0 = time.time()

    from mlx_lm import load, generate

    model, tokenizer = load(args.model)
    t_load = time.time() - t0
    print(f"Model loaded in {t_load:.1f}s")

    # Apply chat template
    messages = [
        {"role": "system", "content": prompt_config["system"]},
        {"role": "user", "content": user_prompt},
    ]
    formatted = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )

    # Count input tokens (approximate)
    input_tokens = len(tokenizer.encode(formatted))
    print(f"Input: ~{input_tokens} tokens, generating up to {args.max_tokens}...")
    print()

    t1 = time.time()
    response = generate(
        model, tokenizer,
        prompt=formatted,
        max_tokens=args.max_tokens,
        verbose=False,
    )
    t_gen = time.time() - t1

    print(f"=== Response ({t_gen:.1f}s) ===")
    print(response)
    print()

    # Parse into parts (matching _parse_parts from sliding_window.py)
    lines = [
        line.strip() for line in response.strip().splitlines()
        if line.strip() and line.strip() != "EMPTY" and len(line.strip()) >= 20
    ]
    print(f"=== Parsed: {len(lines)} parts ===")
    for i, line in enumerate(lines):
        print(f"  [{i}] {line}")


if __name__ == "__main__":
    main()
