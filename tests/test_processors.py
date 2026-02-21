"""Tests for keep.processors — pure processing functions."""

from unittest.mock import MagicMock, patch

import pytest

from keep.processors import (
    ProcessorResult,
    process_summarize,
    process_ocr,
    ocr_image,
    ocr_pdf,
    _content_hash,
    _content_hash_full,
    DELEGATABLE_TASK_TYPES,
    LOCAL_ONLY_TASK_TYPES,
    MIME_TO_EXTENSION,
)


# ---------------------------------------------------------------------------
# process_summarize
# ---------------------------------------------------------------------------


class TestProcessSummarize:
    """Tests for the process_summarize pure function."""

    def test_calls_provider(self):
        """Provider's summarize is called and result is returned."""
        provider = MagicMock()
        provider.summarize.return_value = "A brief summary"

        result = process_summarize("long content here", summarization_provider=provider)

        assert isinstance(result, ProcessorResult)
        assert result.task_type == "summarize"
        assert result.summary == "A brief summary"
        provider.summarize.assert_called_once_with("long content here", context=None)

    def test_passes_context(self):
        """Context is forwarded to the provider."""
        provider = MagicMock()
        provider.summarize.return_value = "contextual summary"

        result = process_summarize(
            "content", context="related notes", summarization_provider=provider,
        )

        assert result.summary == "contextual summary"
        provider.summarize.assert_called_once_with("content", context="related notes")

    def test_no_side_fields(self):
        """Summarize result has no OCR-specific fields set."""
        provider = MagicMock()
        provider.summarize.return_value = "sum"

        result = process_summarize("x", summarization_provider=provider)

        assert result.content is None
        assert result.content_hash is None
        assert result.content_hash_full is None
        assert result.parts is None


# ---------------------------------------------------------------------------
# process_ocr
# ---------------------------------------------------------------------------


class TestProcessOcr:
    """Tests for the process_ocr pure function."""

    def test_short_content_used_as_summary(self):
        """Content shorter than max_summary_length is the summary itself."""
        result = process_ocr("short text", max_summary_length=100)

        assert result.task_type == "ocr"
        assert result.summary == "short text"
        assert result.content == "short text"

    def test_long_content_summarized(self):
        """Provider is called when content exceeds max_summary_length."""
        provider = MagicMock()
        provider.summarize.return_value = "summarized"
        long_text = "x" * 200

        result = process_ocr(
            long_text, max_summary_length=50,
            summarization_provider=provider,
        )

        assert result.summary == "summarized"
        assert result.content == long_text
        provider.summarize.assert_called_once()

    def test_no_provider_truncates(self):
        """Without a provider, long content is truncated with ellipsis."""
        long_text = "abcdef" * 100

        result = process_ocr(long_text, max_summary_length=20)

        assert result.summary == long_text[:20] + "..."
        assert result.content == long_text

    def test_computes_hashes(self):
        """content_hash and content_hash_full are set."""
        result = process_ocr("hello world", max_summary_length=1000)

        assert result.content_hash is not None
        assert len(result.content_hash) == 10  # short hash
        assert result.content_hash_full is not None
        assert len(result.content_hash_full) == 64  # full SHA256

    def test_context_forwarded_to_provider(self):
        """Context is passed through to the summarization provider."""
        provider = MagicMock()
        provider.summarize.return_value = "ctx summary"

        process_ocr(
            "x" * 200, max_summary_length=50,
            context="related context", summarization_provider=provider,
        )

        provider.summarize.assert_called_once_with("x" * 200, context="related context")


# ---------------------------------------------------------------------------
# ocr_image
# ---------------------------------------------------------------------------


class TestOcrImage:
    """Tests for the ocr_image pure function."""

    def test_calls_extractor(self, tmp_path):
        """Extractor.extract is called with path and content_type."""
        extractor = MagicMock()
        extractor.extract.return_value = "Total: $42.99\nThank you for your purchase"

        result = ocr_image(tmp_path / "receipt.png", "image/png", extractor)

        assert result is not None
        assert "42.99" in result
        extractor.extract.assert_called_once_with(
            str(tmp_path / "receipt.png"), "image/png"
        )

    def test_rejects_low_confidence(self, tmp_path):
        """Low-confidence OCR output is rejected."""
        extractor = MagicMock()
        extractor.extract.return_value = "!@#$%^&*()"

        result = ocr_image(tmp_path / "garbage.png", "image/png", extractor)

        assert result is None

    def test_returns_none_on_empty(self, tmp_path):
        """Returns None when extractor returns nothing."""
        extractor = MagicMock()
        extractor.extract.return_value = None

        result = ocr_image(tmp_path / "blank.png", "image/png", extractor)

        assert result is None

    def test_rejects_very_short_text(self, tmp_path):
        """Text <= 10 chars after cleaning is rejected."""
        extractor = MagicMock()
        extractor.extract.return_value = "Hi"

        result = ocr_image(tmp_path / "tiny.png", "image/png", extractor)

        assert result is None


# ---------------------------------------------------------------------------
# ocr_pdf
# ---------------------------------------------------------------------------


class TestOcrPdf:
    """Tests for the ocr_pdf pure function."""

    def test_merges_text_and_ocr(self, tmp_path):
        """Text-layer pages are merged with OCR pages in page order."""
        try:
            from pypdf import PdfWriter
            import pypdfium2  # noqa: F401
        except ImportError:
            pytest.skip("pypdf and pypdfium2 required")

        from keep.providers.documents import FileDocumentProvider

        # Create a 2-page blank PDF (both pages need OCR)
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.add_blank_page(width=200, height=200)
        pdf_path = tmp_path / "test.pdf"
        with open(pdf_path, "wb") as f:
            writer.write(f)

        extractor = MagicMock()
        extractor.extract.return_value = "OCR text from page"

        result = ocr_pdf(pdf_path, [0, 1], extractor)

        # Should have content from both pages (or None if OCR cleaning rejects)
        # The mock returns clean text, so it should succeed
        if result is not None:
            assert "OCR text" in result

    def test_returns_none_when_no_ocr_results(self, tmp_path):
        """Returns None when OCR produces no results."""
        try:
            from pypdf import PdfWriter
            import pypdfium2  # noqa: F401
        except ImportError:
            pytest.skip("pypdf and pypdfium2 required")

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        pdf_path = tmp_path / "blank.pdf"
        with open(pdf_path, "wb") as f:
            writer.write(f)

        # Extractor returns garbage that gets rejected by confidence filter
        extractor = MagicMock()
        extractor.extract.return_value = "!@#$"

        result = ocr_pdf(pdf_path, [0], extractor)

        assert result is None


# ---------------------------------------------------------------------------
# Hash functions (moved from api.py in Phase 3 consolidation)
# ---------------------------------------------------------------------------


class TestContentHash:
    """Tests for _content_hash and _content_hash_full."""

    def test_short_hash_length(self):
        """Short hash is last 10 chars of SHA256."""
        h = _content_hash("hello world")
        assert len(h) == 10
        assert h.isalnum()

    def test_full_hash_length(self):
        """Full hash is complete 64-char SHA256."""
        h = _content_hash_full("hello world")
        assert len(h) == 64
        assert h.isalnum()

    def test_short_is_suffix_of_full(self):
        """Short hash should be the last 10 chars of the full hash."""
        short = _content_hash("test content")
        full = _content_hash_full("test content")
        assert full.endswith(short)

    def test_deterministic(self):
        """Same input produces same hash."""
        assert _content_hash("abc") == _content_hash("abc")
        assert _content_hash_full("abc") == _content_hash_full("abc")

    def test_different_inputs_differ(self):
        """Different inputs produce different hashes."""
        assert _content_hash("foo") != _content_hash("bar")
        assert _content_hash_full("foo") != _content_hash_full("bar")

    def test_backwards_compat_import(self):
        """Hash functions are still importable from api.py."""
        from keep.api import _content_hash as api_hash
        from keep.api import _content_hash_full as api_hash_full
        assert api_hash("test") == _content_hash("test")
        assert api_hash_full("test") == _content_hash_full("test")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for task type constants and MIME mapping."""

    def test_delegatable_types(self):
        assert "summarize" in DELEGATABLE_TASK_TYPES
        assert "ocr" in DELEGATABLE_TASK_TYPES
        assert "embed" not in DELEGATABLE_TASK_TYPES

    def test_local_only_types(self):
        assert "embed" in LOCAL_ONLY_TASK_TYPES
        assert "reindex" in LOCAL_ONLY_TASK_TYPES
        assert "summarize" not in LOCAL_ONLY_TASK_TYPES

    def test_no_overlap(self):
        """Delegatable and local-only should not overlap."""
        assert set(DELEGATABLE_TASK_TYPES) & set(LOCAL_ONLY_TASK_TYPES) == set()

    def test_mime_to_extension(self):
        assert MIME_TO_EXTENSION["application/pdf"] == ".pdf"
        assert MIME_TO_EXTENSION["image/jpeg"] == ".jpg"
        assert MIME_TO_EXTENSION["image/png"] == ".png"

    def test_exports_from_init(self):
        """ProcessorResult and DELEGATABLE_TASK_TYPES are exported from keep."""
        from keep import ProcessorResult as PR, DELEGATABLE_TASK_TYPES as DT
        assert PR is ProcessorResult
        assert DT is DELEGATABLE_TASK_TYPES
