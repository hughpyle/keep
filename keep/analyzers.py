"""
Default analyzer — single-pass LLM decomposition.

Moves the decomposition logic that was previously inline in api.py
into a pluggable AnalyzerProvider implementation.
"""

import json
import logging
import re
from collections.abc import Iterable

from .providers.base import AnalysisChunk, AnalyzerProvider, get_registry

logger = logging.getLogger(__name__)


DECOMPOSITION_SYSTEM_PROMPT = """You are a document analysis assistant. Your task is to decompose a document into its meaningful structural sections.

For each section, provide:
- "summary": A concise summary of the section (1-3 sentences)
- "content": The exact text of the section
- "tags": A dict of relevant tags for this section (optional)

Return a JSON array of section objects. Example:
```json
[
  {"summary": "Introduction and overview of the topic", "content": "The text of section 1...", "tags": {"topic": "overview"}},
  {"summary": "Detailed analysis of the main argument", "content": "The text of section 2...", "tags": {"topic": "analysis"}}
]
```

Guidelines:
- Identify natural section boundaries (headings, topic shifts, structural breaks)
- Each section should be a coherent unit of meaning
- Preserve the original text exactly in the "content" field
- Keep summaries concise but descriptive
- Tags should capture the essence of each section's subject matter
- Return valid JSON only, no commentary outside the JSON array"""


def _parse_decomposition_json(text: str) -> list[dict]:
    """
    Parse JSON from LLM decomposition output.

    Handles:
    - Code fences (```json ... ```)
    - Wrapper objects ({"sections": [...]})
    - Direct JSON arrays

    Args:
        text: Raw LLM output

    Returns:
        List of section dicts
    """
    if not text:
        return []

    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        # Remove first line (```json or ```) and last line (```)
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse decomposition JSON")
        return []

    # Handle wrapper objects like {"sections": [...]}
    if isinstance(data, dict):
        for key in ("sections", "parts", "chunks", "result", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            return []

    if not isinstance(data, list):
        return []

    # Validate and normalize entries
    result = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        # Must have at least summary or content
        if not entry.get("summary") and not entry.get("content"):
            continue
        section = {
            "summary": str(entry.get("summary", "")),
            "content": str(entry.get("content", "")),
        }
        if entry.get("tags") and isinstance(entry["tags"], dict):
            section["tags"] = {str(k): str(v) for k, v in entry["tags"].items()}
        result.append(section)

    return result


def _simple_chunk_decomposition(content: str) -> list[dict]:
    """
    Paragraph-based fallback when no LLM is available.

    Splits content on double-newlines, groups small paragraphs together.
    Each chunk gets a truncated summary.
    """
    paragraphs = re.split(r'\n\s*\n', content.strip())
    if not paragraphs:
        return []

    # Group small paragraphs together (min ~200 chars per chunk)
    chunks = []
    current = []
    current_len = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        current.append(para)
        current_len += len(para)
        if current_len >= 500:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
    if current:
        chunks.append("\n\n".join(current))

    # If we ended up with just 1 chunk that is the whole content, not useful
    if len(chunks) <= 1:
        return []

    result = []
    for chunk in chunks:
        summary = chunk[:200].rsplit(" ", 1)[0] + "..." if len(chunk) > 200 else chunk
        result.append({
            "summary": summary,
            "content": chunk,
        })
    return result


class DefaultAnalyzer:
    """
    Single-pass LLM decomposition (current behavior).

    Concatenates all chunks, sends to the configured summarization provider's
    generate() method, and parses the resulting JSON into part dicts.
    Falls back to simple paragraph-based chunking if LLM is unavailable.
    """

    def __init__(self, provider=None):
        """
        Args:
            provider: A SummarizationProvider with generate() support.
                      Passed at construction, not per-call.
        """
        self._provider = provider

    def analyze(
        self,
        chunks: Iterable[AnalysisChunk],
        guide_context: str = "",
    ) -> list[dict]:
        """Decompose content chunks into parts."""
        # Materialise chunks and concatenate content
        chunk_list = list(chunks)
        content = "\n\n---\n\n".join(c.content for c in chunk_list)

        raw = self._call_llm(content, guide_context)
        if not raw:
            raw = _simple_chunk_decomposition(content)
        return raw

    def _call_llm(self, content: str, guide_context: str = "") -> list[dict]:
        """Call the LLM to decompose content into sections."""
        provider = self._provider
        if provider is None:
            return []

        # Unwrap lock wrapper to access underlying provider
        if hasattr(provider, '_provider') and provider._provider is not None:
            provider = provider._provider

        # Truncate content for decomposition
        truncated = content[:80000] if len(content) > 80000 else content

        # Build user prompt
        user_prompt = truncated
        if guide_context:
            user_prompt = (
                f"Decompose this document into meaningful sections.\n\n"
                f"Use these tag definitions to guide your tagging:\n\n"
                f"{guide_context}\n\n"
                f"---\n\n"
                f"Document to analyze:\n\n{truncated}"
            )

        try:
            result = provider.generate(
                DECOMPOSITION_SYSTEM_PROMPT,
                user_prompt,
                max_tokens=4096,
            )
            if result:
                return _parse_decomposition_json(result)

            logger.warning(
                "Provider %s returned no result for decomposition, "
                "falling back to simple chunking",
                type(provider).__name__,
            )
            return []

        except Exception as e:
            logger.warning("LLM decomposition failed: %s", e)
            return []


# Register with the provider registry on import
get_registry().register_analyzer("default", DefaultAnalyzer)
