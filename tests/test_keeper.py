"""Tests for Keeper class.

These tests use a real Keeper with real embedding providers.
They are skipped when no embedding provider is available (no API keys,
no local ML extras). Run with `keep-skill[local]` installed or with
an API key set to enable these tests.
"""

import tempfile
from pathlib import Path

import pytest

from keep.api import Keeper


def _has_embedding_provider() -> bool:
    """Check if an embedding provider can be configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            kp = Keeper(store_path=Path(tmpdir))
            if kp._config.embedding is None:
                kp.close()
                return False
            kp.close()
            return True
        except Exception:
            return False


pytestmark = pytest.mark.skipif(
    not _has_embedding_provider(),
    reason="No embedding provider available (install keep-skill[local] or set API key)",
)


@pytest.fixture(scope="module")
def keeper():
    """
    Create a single Keeper instance for all tests in module.

    Uses module scope to load the model only once.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        kp = Keeper(store_path=Path(tmpdir))
        yield kp
        kp.close()


class TestKeeperBasics:
    """Basic Keeper operations."""

    def test_remember_returns_item(self, keeper: Keeper) -> None:
        """remember() returns an Item with id and summary."""
        item = keeper.remember("Test content for remember.")

        assert item.id is not None
        assert item.summary is not None
        assert len(item.summary) > 0

    def test_remember_with_explicit_id(self, keeper: Keeper) -> None:
        """remember() with explicit id uses that id."""
        item = keeper.remember("Content with explicit id.", id="test:explicit")

        assert item.id == "test:explicit"

    def test_remember_with_tags(self, keeper: Keeper) -> None:
        """remember() stores provided tags."""
        item = keeper.remember(
            "Content with tags.",
            id="test:tagged",
            tags={"category": "test", "priority": "high"},
        )

        assert item.tags["category"] == "test"
        assert item.tags["priority"] == "high"

    def test_get_retrieves_item(self, keeper: Keeper) -> None:
        """get() retrieves a stored item by id."""
        keeper.remember("Retrievable content.", id="test:retrieve")

        item = keeper.get("test:retrieve")

        assert item is not None
        assert item.id == "test:retrieve"

    def test_get_returns_none_for_missing(self, keeper: Keeper) -> None:
        """get() returns None for non-existent id."""
        item = keeper.get("nonexistent:id")

        assert item is None

    def test_exists_true_for_stored(self, keeper: Keeper) -> None:
        """exists() returns True for stored item."""
        keeper.remember("Existence test.", id="test:exists")

        assert keeper.exists("test:exists") is True

    def test_exists_false_for_missing(self, keeper: Keeper) -> None:
        """exists() returns False for missing item."""
        assert keeper.exists("nonexistent:missing") is False

    def test_delete_removes_item(self, keeper: Keeper) -> None:
        """delete() removes an item."""
        keeper.remember("To be deleted.", id="test:delete")
        assert keeper.exists("test:delete") is True

        keeper.delete("test:delete")

        assert keeper.exists("test:delete") is False

    def test_count_returns_item_count(self, keeper: Keeper) -> None:
        """count() returns number of items."""
        initial = keeper.count()

        keeper.remember("Count test 1.", id="test:count1")
        keeper.remember("Count test 2.", id="test:count2")

        assert keeper.count() >= initial + 2


class TestKeeperFind:
    """Keeper find/search operations."""

    def test_find_returns_results(self, keeper: Keeper) -> None:
        """find() returns matching items."""
        keeper.remember(
            "The quick brown fox jumps over the lazy dog.",
            id="test:fox",
        )

        results = keeper.find("fox jumping")

        assert len(results) > 0
        assert any(r.id == "test:fox" for r in results)

    def test_find_results_have_scores(self, keeper: Keeper) -> None:
        """find() results include similarity scores."""
        keeper.remember("Scored content for testing.", id="test:scored")

        results = keeper.find("scored content")

        assert len(results) > 0
        assert results[0].score is not None

    def test_find_respects_limit(self, keeper: Keeper) -> None:
        """find() respects limit parameter."""
        results = keeper.find("test", limit=2)

        assert len(results) <= 2


class TestKeeperUpdate:
    """Keeper update (tag merge) behavior."""

    def test_remember_merges_tags(self, keeper: Keeper) -> None:
        """remember() merges tags on update."""
        keeper.remember(
            "Original content.",
            id="test:merge",
            tags={"a": "1", "b": "2"},
        )

        keeper.remember(
            "Updated content.",
            id="test:merge",
            tags={"b": "updated", "c": "3"},
        )

        item = keeper.get("test:merge")
        assert item.tags["a"] == "1"  # preserved
        assert item.tags["b"] == "updated"  # updated
        assert item.tags["c"] == "3"  # added
