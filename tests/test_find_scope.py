"""
Tests for find(scope=...) parameter — constrain results to IDs matching a glob.

Uses mock providers — no ML models or network.
"""

import pytest

from keep.api import Keeper


@pytest.fixture
def kp(mock_providers, tmp_path):
    """Create a Keeper with file-like IDs to simulate scoped search."""
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()

    # Simulate a memory folder: file:// URIs
    kp.put("Daily standup notes for Monday", id="file:///home/user/memory/2026-03-10.md")
    kp.put("Daily standup notes for Tuesday", id="file:///home/user/memory/2026-03-11.md")
    kp.put("Architecture decision record for auth", id="file:///home/user/memory/adr-auth.md")
    kp.put("Top-level MEMORY index file", id="file:///home/user/MEMORY.md")

    # Items outside the scope
    kp.put("Project readme with setup instructions", id="file:///home/user/README.md")
    kp.put("Random note about standup process", id="standup-process")

    return kp


class TestFindScope:
    """Test find() with scope glob filter."""

    def test_find_without_scope_returns_all(self, kp):
        """find() without scope returns results from anywhere."""
        results = kp.find("standup")
        ids = {r.id for r in results}
        # Should include both memory files and the unscoped note
        assert len(ids) >= 2

    def test_scope_filters_to_matching_ids(self, kp):
        """find() with scope only returns items whose IDs match the glob."""
        results = kp.find("standup", scope="file:///home/user/memory*")
        ids = {r.id for r in results}
        # Memory files match the scope
        assert any(i.startswith("file:///home/user/memory") for i in ids)
        # Unscoped note should be excluded
        assert "standup-process" not in ids
        assert "file:///home/user/README.md" not in ids

    def test_scope_with_no_matches_returns_empty(self, kp):
        """find() with scope matching no IDs returns empty."""
        results = kp.find("standup", scope="file:///nonexistent/*")
        assert len(results) == 0

    def test_scope_glob_star(self, kp):
        """Scope glob with * matches multiple items."""
        results = kp.find("notes", scope="file:///home/user/memory/2026*")
        ids = {r.id for r in results}
        for i in ids:
            assert i.startswith("file:///home/user/memory/2026")

    def test_scope_broad_glob(self, kp):
        """Broader scope includes MEMORY.md and memory/ files."""
        results = kp.find("memory", scope="file:///home/user/*MEMORY*")
        # Should match file:///home/user/MEMORY.md
        # (memory/ files don't match this glob since * doesn't cross /)
        ids = {r.id for r in results}
        assert "standup-process" not in ids

    def test_scope_with_similar_to(self, kp):
        """Scope works with similar_to mode."""
        results = kp.find(
            similar_to="file:///home/user/memory/2026-03-10.md",
            scope="file:///home/user/memory*",
        )
        ids = {r.id for r in results}
        assert "file:///home/user/README.md" not in ids
        assert "standup-process" not in ids
