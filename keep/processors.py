"""
Pure processing functions for keep.

These functions encapsulate the "compute" portion of background processing
(summarization, OCR) without any store reads or writes.  This separation
allows the same processing logic to run locally or be delegated to a
hosted service (Phase 1 of Hybrid Processing).

Each function returns a ProcessorResult that the caller applies to the store
via Keeper.apply_result().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .api import _content_hash, _content_hash_full

logger = logging.getLogger(__name__)


@dataclass
class ProcessorResult:
    """Result of processing a task.  Caller applies to store."""

    task_type: str  # "summarize" | "ocr" | "analyze"
    summary: str | None = None
    content: str | None = None            # ocr: full extracted text
    content_hash: str | None = None       # ocr: short hash
    content_hash_full: str | None = None  # ocr: full SHA256
    parts: list | None = None             # analyze: PartInfo list (Phase 2)


def process_summarize(
    content: str,
    *,
    context: str | None = None,
    summarization_provider,
) -> ProcessorResult:
    """Summarize content.  Pure function — no store access."""
    summary = summarization_provider.summarize(content, context=context)
    return ProcessorResult(task_type="summarize", summary=summary)


def ocr_image(path: Path, content_type: str, extractor) -> str | None:
    """OCR a single image file.  Returns cleaned text or None."""
    from .providers.documents import FileDocumentProvider

    text = extractor.extract(str(path), content_type)
    if not text:
        return None
    cleaned = FileDocumentProvider._clean_ocr_text(text)
    confidence = FileDocumentProvider._estimate_ocr_confidence(cleaned)
    if confidence < 0.3 or len(cleaned) <= 10:
        logger.info("Image OCR low confidence (%.2f) for %s", confidence, path.name)
        return None
    return cleaned


def ocr_pdf(path: Path, ocr_pages: list[int], extractor) -> str | None:
    """OCR scanned PDF pages and merge with text-layer pages.  Returns text or None."""
    from .providers.documents import FileDocumentProvider

    file_provider = FileDocumentProvider()
    ocr_results = file_provider._ocr_pdf_pages(path, ocr_pages, extractor=extractor)

    if not ocr_results:
        return None

    # Re-extract text pages (fast) and merge with OCR
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text_parts: list[tuple[int, str]] = []
    ocr_set = set(ocr_pages)
    for i, page in enumerate(reader.pages):
        if i not in ocr_set:
            text = page.extract_text()
            if text and text.strip():
                text_parts.append((i, text))
    text_parts.extend(ocr_results)
    text_parts.sort(key=lambda t: t[0])
    return "\n\n".join(text for _, text in text_parts)


def process_ocr(
    full_content: str,
    *,
    max_summary_length: int,
    context: str | None = None,
    summarization_provider=None,
) -> ProcessorResult:
    """Process OCR'd text: summarize if needed, compute hashes.

    Pure function — no store access.
    """
    if len(full_content) <= max_summary_length:
        summary = full_content
    elif summarization_provider:
        summary = summarization_provider.summarize(full_content, context=context)
    else:
        summary = full_content[:max_summary_length] + "..."

    return ProcessorResult(
        task_type="ocr",
        summary=summary,
        content=full_content,
        content_hash=_content_hash(full_content),
        content_hash_full=_content_hash_full(full_content),
    )
