"""
Tests for scoped now (get_now/set_now with scope parameter).

Uses mock providers — no ML models or network.
"""

import pytest

from keep.api import Keeper


@pytest.fixture
def kp(mock_providers, tmp_path):
    """Create a Keeper instance."""
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()
    return kp


class TestScopedNow:
    """Test get_now/set_now with scope parameter."""

    def test_default_now_unchanged(self, kp):
        """get_now() without scope returns singleton."""
        item = kp.get_now()
        assert item.id == "now"

    def test_scoped_now_creates_separate_doc(self, kp):
        """get_now(scope='alice') creates now:alice."""
        item = kp.get_now(scope="alice")
        assert item.id == "now:alice"

    def test_scoped_now_isolation(self, kp):
        """Different scopes have independent now docs."""
        kp.set_now("Alice is working on X", scope="alice")
        kp.set_now("Bob is working on Y", scope="bob")

        alice = kp.get_now(scope="alice")
        bob = kp.get_now(scope="bob")

        assert "Alice" in alice.summary
        assert "Bob" in bob.summary
        assert alice.id != bob.id

    def test_scoped_now_auto_tags_user(self, kp):
        """set_now(scope='alice') auto-sets user=alice tag."""
        kp.set_now("Working on tests", scope="alice")
        item = kp.get_now(scope="alice")
        assert item.tags.get("user") == "alice"

    def test_scoped_now_preserves_explicit_tags(self, kp):
        """Explicit tags are preserved alongside auto user tag."""
        kp.set_now("Working on tests", scope="alice", tags={"project": "keep"})
        item = kp.get_now(scope="alice")
        assert item.tags.get("user") == "alice"
        assert item.tags.get("project") == "keep"

    def test_scoped_now_doesnt_affect_singleton(self, kp):
        """Setting scoped now doesn't touch the singleton."""
        kp.set_now("Singleton context")
        kp.set_now("Alice context", scope="alice")

        singleton = kp.get_now()
        assert "Singleton" in singleton.summary
        assert singleton.id == "now"

    def test_explicit_user_tag_not_overwritten(self, kp):
        """If user tag is explicitly set, scope doesn't overwrite it."""
        kp.set_now("Custom user", scope="alice", tags={"user": "custom"})
        item = kp.get_now(scope="alice")
        # setdefault means explicit tag wins
        assert item.tags.get("user") == "custom"
