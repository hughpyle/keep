"""
Tests for required_tags config enforcement.

Uses mock providers — no ML models or network.
"""

import pytest

from keep.api import Keeper


@pytest.fixture
def kp_with_required(mock_providers, tmp_path):
    """Create a Keeper with required_tags=["user"]."""
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()
    kp._config.required_tags = ["user"]
    return kp


@pytest.fixture
def kp_no_required(mock_providers, tmp_path):
    """Create a Keeper without required tags."""
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()
    return kp


class TestRequiredTags:
    """Test required_tags enforcement on put()."""

    def test_put_with_required_tag_succeeds(self, kp_with_required):
        """put() with required tag succeeds."""
        item = kp_with_required.put("Hello", id="test:1", tags={"user": "alice"})
        assert item.id == "test:1"

    def test_put_without_required_tag_raises(self, kp_with_required):
        """put() without required tag raises ValueError."""
        with pytest.raises(ValueError, match="Required tags missing: user"):
            kp_with_required.put("Hello", id="test:1")

    def test_put_with_wrong_tags_raises(self, kp_with_required):
        """put() with other tags but not required one raises."""
        with pytest.raises(ValueError, match="Required tags missing: user"):
            kp_with_required.put("Hello", id="test:1", tags={"project": "keep"})

    def test_system_docs_exempt(self, kp_with_required):
        """System docs (dot-prefix IDs) skip required tag validation."""
        item = kp_with_required.put("System doc", id=".meta/test")
        assert item.id == ".meta/test"

    def test_no_required_tags_allows_anything(self, kp_no_required):
        """Without required_tags config, put() works without tags."""
        item = kp_no_required.put("Hello", id="test:1")
        assert item.id == "test:1"

    def test_required_tags_default_empty(self, kp_no_required):
        """required_tags defaults to empty list."""
        assert kp_no_required._config.required_tags == []
