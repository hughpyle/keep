"""
Tests for find(tags=...) pre-filter parameter.

Uses mock providers — no ML models or network.
"""

import pytest

from keep.api import Keeper


@pytest.fixture
def kp(mock_providers, tmp_path):
    """Create a Keeper with tagged seed data."""
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()

    # Seed items with different tags
    kp.put("Alice likes cats and dogs", id="alice:pets", tags={"user": "alice", "topic": "pets"})
    kp.put("Alice works on project X", id="alice:work", tags={"user": "alice", "topic": "work"})
    kp.put("Bob likes birds", id="bob:pets", tags={"user": "bob", "topic": "pets"})
    kp.put("Bob works on project Y", id="bob:work", tags={"user": "bob", "topic": "work"})

    return kp


class TestFindTagsFilter:
    """Test find() with tags pre-filter."""

    def test_find_without_tags_returns_all(self, kp):
        """find() without tags returns results from all users."""
        results = kp.find("pets")
        ids = {r.id for r in results}
        assert "alice:pets" in ids
        assert "bob:pets" in ids

    def test_find_with_single_tag_filters(self, kp):
        """find() with tags={user: alice} only returns alice's items."""
        results = kp.find("pets", tags={"user": "alice"})
        ids = {r.id for r in results}
        assert "alice:pets" in ids
        assert "bob:pets" not in ids

    def test_find_with_multiple_tags(self, kp):
        """find() with multiple tags filters by all of them."""
        results = kp.find("work", tags={"user": "alice", "topic": "work"})
        ids = {r.id for r in results}
        assert "alice:work" in ids
        assert "bob:work" not in ids
        assert "alice:pets" not in ids

    def test_find_tags_case_insensitive(self, kp):
        """Tags are casefolded before filtering."""
        results = kp.find("pets", tags={"User": "Alice"})
        ids = {r.id for r in results}
        assert "alice:pets" in ids
        assert "bob:pets" not in ids

    def test_find_tags_no_match(self, kp):
        """find() with non-matching tags returns empty."""
        results = kp.find("pets", tags={"user": "charlie"})
        assert len(results) == 0

    def test_find_fulltext_with_tags(self, kp):
        """find() fulltext mode also respects tags filter."""
        results = kp.find("cats", fulltext=True, tags={"user": "alice"})
        ids = {r.id for r in results}
        assert "alice:pets" in ids
        assert "bob:pets" not in ids

    def test_find_tags_none_is_noop(self, kp):
        """find() with tags=None is same as no tags."""
        results_none = kp.find("pets", tags=None)
        results_default = kp.find("pets")
        assert {r.id for r in results_none} == {r.id for r in results_default}
