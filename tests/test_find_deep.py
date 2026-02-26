"""Tests for find --deep (tag-following multi-hop search)."""

import pytest
from keep.api import Keeper, FindResults


@pytest.fixture
def kp(mock_providers, tmp_path):
    """Create a Keeper with seed data for deep tag-follow tests.

    Items are inserted in a specific order so that bridge items (b, c, d)
    fall past the fetch_limit boundary in the mock store.  The mock's
    query_embedding returns items in insertion order, so fillers between
    the primary match and bridge items push them out of primary results.
    """
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()

    # A: primary match — will be in primary results
    kp.put("OAuth2 token design for project X", id="a",
           tags={"project": "x", "topic": "auth"})

    # Filler items to push bridge items past fetch_limit.
    # Deep uses fetch_limit = max(limit*3, 30).  We need >29 fillers
    # so bridge items are beyond index 30 in the mock store.
    for i in range(35):
        kp.put(f"Unrelated filler note number {i}", id=f"filler-{i}",
               tags={"filler": "yes"})

    # Bridge items — only discoverable via deep tag-follow
    kp.put("Project X reduced latency by 40%", id="b",
           tags={"project": "x"})
    kp.put("Alice recommended Redis for project X caching", id="c",
           tags={"project": "x", "topic": "caching"})
    kp.put("Auth best practices and token rotation", id="d",
           tags={"topic": "auth"})

    # E: unrelated — should NOT be discovered via tag-follow
    kp.put("Weekly standup meeting notes", id="e",
           tags={"category": "meetings"})

    return kp


def _all_deep_ids(results):
    """Collect all IDs from deep_groups."""
    ids = set()
    for group in results.deep_groups.values():
        ids.update(item.id for item in group)
    return ids


class TestDeepTagFollow:
    def test_deep_surfaces_tag_siblings(self, kp):
        """deep=True discovers bridge items via followed tags."""
        results = kp.find("OAuth2 auth token design", deep=True, limit=5)
        deep_ids = _all_deep_ids(results)
        # B, C share project=x with A; D shares topic=auth with A
        assert "b" in deep_ids, "B should be discovered via project=x"
        assert "c" in deep_ids, "C should be discovered via project=x"
        assert "d" in deep_ids, "D should be discovered via topic=auth"
        # E has no tag overlap with primary results
        assert "e" not in deep_ids, "E should not appear (no tag overlap)"

    def test_deep_groups_nested_under_primary(self, kp):
        """Deep items are grouped under the primary result that led to them."""
        results = kp.find("OAuth2 auth token design", deep=True, limit=5)
        assert isinstance(results, FindResults)
        # A has tags project=x and topic=auth, so its deep group should exist
        assert "a" in results.deep_groups
        group_ids = {item.id for item in results.deep_groups["a"]}
        assert "b" in group_ids or "c" in group_ids or "d" in group_ids

    def test_deep_false_unchanged(self, kp):
        """deep=False returns only primary results (no deep_groups)."""
        baseline = kp.find("OAuth2 auth token design", deep=False, limit=5)
        baseline_ids = {r.id for r in baseline}
        # Without deep, bridge items past fetch_limit are not reachable
        assert "b" not in baseline_ids
        assert "c" not in baseline_ids
        assert "d" not in baseline_ids
        assert baseline.deep_groups == {}

    def test_deep_no_duplicates(self, kp):
        """Primary items should not appear in deep groups."""
        results = kp.find("OAuth2 auth token design", deep=True, limit=10)
        primary_ids = {r.id for r in results}
        deep_ids = _all_deep_ids(results)
        assert not primary_ids & deep_ids, "Deep items should not duplicate primaries"

    def test_deep_noop_for_fulltext(self, kp):
        """deep is silently ignored for fulltext search (no embedding)."""
        results = kp.find("OAuth2", fulltext=True, deep=True, limit=10)
        assert isinstance(results, list)
        assert results.deep_groups == {}

    def test_deep_with_similar_to(self, kp):
        """deep works with similar_to (find --id)."""
        results = kp.find(similar_to="a", deep=True, limit=10)
        assert isinstance(results, FindResults)
        # Should have results and possibly deep groups
        assert len(results) > 0

    def test_deep_no_system_tags_followed(self, kp):
        """Tags starting with _ should not be followed."""
        # All items share system tags (_created, _updated, _source).
        # If system tags were followed, E would be discovered via those.
        results = kp.find("OAuth2 auth token design", deep=True, limit=5)
        deep_ids = _all_deep_ids(results)
        assert "e" not in deep_ids, "E should not appear via system tag follow"

    def test_deep_no_cross_group_duplicates(self, kp):
        """Each deep item should appear under exactly one primary."""
        results = kp.find("OAuth2 auth token design", deep=True, limit=5)
        seen = set()
        for group in results.deep_groups.values():
            for item in group:
                assert item.id not in seen, f"{item.id} appears in multiple deep groups"
                seen.add(item.id)

    def test_deep_prefers_rare_tag_overlap(self, kp):
        """IDF weighting ranks rare-tag matches above common-tag matches."""
        # In the fixture: project=x appears on a, b, c (df=3)
        #                 topic=auth appears on a, d (df=2, rarer)
        # With IDF: d (topic=auth, higher IDF) should score above b (project=x only)
        results = kp.find("OAuth2 auth token design", deep=True, limit=5)
        group = results.deep_groups.get("a", [])
        ids = [item.id for item in group]
        assert "d" in ids and "b" in ids, "Both d and b should be in deep group"
        assert ids.index("d") < ids.index("b"), \
            "d (rare topic=auth) should rank above b (common project=x)"

    def test_deep_no_user_tags_noop(self, mock_providers, tmp_path):
        """When results have no user tags, deep is a no-op."""
        kp = Keeper(store_path=tmp_path)
        kp._get_embedding_provider()

        # Items with only system tags (none explicitly set)
        kp.put("Item with no user tags", id="no-tags-1")
        kp.put("Another item with no user tags", id="no-tags-2")

        results = kp.find("item with no user tags", deep=True, limit=10)
        assert results.deep_groups == {}
