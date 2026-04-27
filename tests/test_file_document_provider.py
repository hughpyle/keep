"""Tests for filesystem-backed document ingestion."""

import logging

import pytest

from keep.providers.documents import FileDocumentProvider


def test_file_provider_warns_when_following_symlink(tmp_path, monkeypatch, caplog):
    """Symlink traversal stays allowed, but it must be visible in logs."""
    target = tmp_path / "target.txt"
    target.write_text("hello through link", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    monkeypatch.setattr(
        "keep.providers.documents.validate_path_within_home",
        lambda path: path,
    )
    caplog.set_level(logging.WARNING, logger="keep.providers.documents")

    doc = FileDocumentProvider().fetch(f"file://{link}")

    assert "hello through link" in doc.content
    assert any("File ingest followed symlink" in rec.message for rec in caplog.records)
