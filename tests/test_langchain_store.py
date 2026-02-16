"""
Tests for KeepStore (LangGraph BaseStore).

Uses mock providers — no ML models or network.
"""

import json

import pytest

from keep.api import Keeper
from keep.langchain.store import (
    KeepStore,
    _DATA_TAG,
    _SOURCE_TAG,
    _SOURCE_VALUE,
    _DEFAULT_NS_KEYS,
    _namespace_to_id,
    _id_to_namespace_key,
    _namespace_to_tags,
    _tags_to_namespace,
    _infer_depth,
    _matches_condition,
)

# Skip entire module if langgraph is not installed
langgraph = pytest.importorskip("langgraph")

from langgraph.store.base import (
    GetOp,
    Item as LGItem,
    ListNamespacesOp,
    MatchCondition,
    PutOp,
    SearchOp,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def keeper(mock_providers, tmp_path):
    """Create a Keeper with mocked backends."""
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()
    return kp


@pytest.fixture
def store(keeper):
    """Create a KeepStore wrapping the test Keeper."""
    return KeepStore(keeper=keeper)


@pytest.fixture
def two_level_store(keeper):
    """Create a KeepStore with two-level namespace keys."""
    return KeepStore(keeper=keeper, namespace_keys=["category", "user"])


@pytest.fixture
def scoped_store(keeper):
    """Create a KeepStore with user_id scoping and category namespace."""
    return KeepStore(
        keeper=keeper, user_id="alice", namespace_keys=["category"],
    )


# ── Helper tests ─────────────────────────────────────────────────────

class TestHelpers:

    def test_namespace_to_id(self):
        assert _namespace_to_id(("memories", "alice"), "fact-1") == "memories/alice/fact-1"

    def test_namespace_to_id_single(self):
        assert _namespace_to_id(("notes",), "doc-1") == "notes/doc-1"

    def test_namespace_to_id_empty_namespace(self):
        assert _namespace_to_id((), "key") == "key"

    def test_id_to_namespace_key_with_depth(self):
        ns, key = _id_to_namespace_key("memories/alice/fact-1", depth=2)
        assert ns == ("memories", "alice")
        assert key == "fact-1"

    def test_id_to_namespace_key_depth_zero(self):
        ns, key = _id_to_namespace_key("key-only", depth=0)
        assert ns == ()
        assert key == "key-only"

    def test_id_to_namespace_key_depth_one(self):
        ns, key = _id_to_namespace_key("alice/fact-1", depth=1)
        assert ns == ("alice",)
        assert key == "fact-1"

    def test_id_to_namespace_key_compound_key(self):
        """Key containing '/' is preserved when depth is known."""
        ns, key = _id_to_namespace_key("ns/sub/part-a/part-b", depth=2)
        assert ns == ("ns", "sub")
        assert key == "part-a/part-b"

    def test_namespace_to_tags(self):
        tags = _namespace_to_tags(("memories", "alice"), ["category", "user"])
        assert tags == {"category": "memories", "user": "alice"}

    def test_namespace_to_tags_partial(self):
        tags = _namespace_to_tags(("memories",), ["category", "user"])
        assert tags == {"category": "memories"}

    def test_namespace_to_tags_empty(self):
        tags = _namespace_to_tags((), ["category", "user"])
        assert tags == {}

    def test_tags_to_namespace(self):
        ns = _tags_to_namespace(
            {"category": "memories", "user": "alice"},
            ["category", "user"],
        )
        assert ns == ("memories", "alice")

    def test_tags_to_namespace_partial(self):
        ns = _tags_to_namespace(
            {"category": "memories"},
            ["category", "user"],
        )
        assert ns == ("memories",)

    def test_tags_to_namespace_gap_stops(self):
        """Missing intermediate key stops reconstruction."""
        ns = _tags_to_namespace(
            {"user": "alice"},
            ["category", "user"],
        )
        assert ns == ()

    def test_infer_depth(self):
        assert _infer_depth(
            {"category": "memories", "user": "alice"},
            ["category", "user"],
        ) == 2

    def test_infer_depth_partial(self):
        assert _infer_depth(
            {"category": "memories"},
            ["category", "user"],
        ) == 1

    def test_infer_depth_empty(self):
        assert _infer_depth({}, ["category", "user"]) == 0

    def test_matches_condition_prefix(self):
        assert _matches_condition(("memories", "alice"), MatchCondition("prefix", ("memories",)))
        assert not _matches_condition(("facts", "alice"), MatchCondition("prefix", ("memories",)))

    def test_matches_condition_suffix(self):
        assert _matches_condition(("memories", "alice"), MatchCondition("suffix", ("alice",)))
        assert not _matches_condition(("memories", "bob"), MatchCondition("suffix", ("alice",)))

    def test_matches_condition_wildcard(self):
        assert _matches_condition(("memories", "alice"), MatchCondition("prefix", ("*", "alice")))


# ── Put / Get tests (default namespace_keys=["user"]) ───────────────

class TestPutGet:

    def test_put_and_get(self, store):
        """Basic put/get round-trip with default single-level namespace."""
        store.put(("alice",), "fact-1", {"content": "likes coffee"})
        item = store.get(("alice",), "fact-1")

        assert item is not None
        assert item.key == "fact-1"
        assert item.namespace == ("alice",)
        assert item.value["content"] == "likes coffee"

    def test_get_nonexistent(self, store):
        """get() returns None for missing items."""
        item = store.get(("alice",), "missing")
        assert item is None

    def test_put_overwrites(self, store):
        """Repeated put updates the value."""
        store.put(("ns",), "k", {"content": "first"})
        store.put(("ns",), "k", {"content": "second"})
        item = store.get(("ns",), "k")
        assert item.value["content"] == "second"

    def test_put_complex_value(self, store):
        """Values with multiple string fields are preserved as tags."""
        store.put(("ns",), "k", {"content": "test", "importance": "high"})
        item = store.get(("ns",), "k")
        assert item.value["content"] == "test"
        assert item.value["importance"] == "high"

    def test_value_with_non_string_types(self, store):
        """Non-string values survive via _keep_data JSON overflow."""
        value = {"content": "hello", "score": 0.95, "active": True}
        store.put(("ns",), "k", value)
        item = store.get(("ns",), "k")
        assert item.value["score"] == 0.95
        assert item.value["active"] is True
        assert item.value["content"] == "hello"

    def test_put_stores_namespace_as_regular_tag(self, store):
        """Namespace components become regular Keep tags."""
        store.put(("alice",), "fact-1", {"content": "test"})
        raw = store.keeper.get("alice/fact-1")
        # Default namespace_keys=["user"], so position 0 → user tag
        assert raw.tags.get("user") == "alice"

    def test_put_stores_source_tag(self, store):
        """KeepStore items have _source=langchain marker."""
        store.put(("ns",), "k", {"content": "test"})
        raw = store.keeper.get("ns/k")
        assert raw.tags.get(_SOURCE_TAG) == _SOURCE_VALUE

    def test_put_stores_data_tag_only_when_needed(self, store):
        """_keep_data tag only present for non-string values."""
        store.put(("ns",), "k1", {"content": "all strings", "tag": "value"})
        raw1 = store.keeper.get("ns/k1")
        assert _DATA_TAG not in raw1.tags

        store.put(("ns",), "k2", {"content": "mixed", "score": 0.5})
        raw2 = store.keeper.get("ns/k2")
        assert _DATA_TAG in raw2.tags
        assert json.loads(raw2.tags[_DATA_TAG]) == {"score": 0.5}


# ── Two-level namespace tests ──────────────────────────────────────

class TestTwoLevelNamespace:

    def test_two_level_put_get(self, two_level_store):
        """Two-level namespace maps to two regular tags."""
        two_level_store.put(
            ("memories", "alice"), "fact-1",
            {"content": "likes coffee"},
        )
        item = two_level_store.get(("memories", "alice"), "fact-1")
        assert item is not None
        assert item.value["content"] == "likes coffee"

    def test_two_level_stores_both_tags(self, two_level_store):
        """Both namespace components stored as regular tags."""
        two_level_store.put(
            ("memories", "alice"), "fact-1",
            {"content": "test"},
        )
        raw = two_level_store.keeper.get("memories/alice/fact-1")
        assert raw.tags.get("category") == "memories"
        assert raw.tags.get("user") == "alice"

    def test_partial_namespace(self, two_level_store):
        """Partial namespace only sets the first tag."""
        two_level_store.put(("memories",), "fact-1", {"content": "test"})
        raw = two_level_store.keeper.get("memories/fact-1")
        assert raw.tags.get("category") == "memories"
        assert raw.tags.get("user", "") == ""


# ── Delete tests ─────────────────────────────────────────────────────

class TestDelete:

    def test_delete(self, store):
        """delete() removes the item."""
        store.put(("ns",), "k", {"content": "hello"})
        store.delete(("ns",), "k")
        assert store.get(("ns",), "k") is None

    def test_delete_nonexistent(self, store):
        """delete() on missing item doesn't raise."""
        store.delete(("ns",), "missing")  # should not raise


# ── Search tests ─────────────────────────────────────────────────────

class TestSearch:

    def test_search_with_query(self, two_level_store):
        """search() with query returns matching items."""
        two_level_store.put(("facts", "alice"), "f1", {"content": "likes coffee"})
        two_level_store.put(("facts", "alice"), "f2", {"content": "prefers dark mode"})

        results = two_level_store.search(("facts", "alice"), query="coffee")
        assert len(results) > 0
        assert all(hasattr(r, "score") for r in results)

    def test_search_scoped_by_namespace(self, two_level_store):
        """search() filters by namespace prefix."""
        two_level_store.put(("facts", "alice"), "f1", {"content": "alice fact"})
        two_level_store.put(("facts", "bob"), "f2", {"content": "bob fact"})

        results = two_level_store.search(("facts", "alice"), query="fact")
        ids = [r.key for r in results]
        assert "f1" in ids

    def test_search_no_query_returns_by_tags(self, two_level_store):
        """search() without query falls back to tag retrieval."""
        two_level_store.put(("ns", "x"), "k1", {"content": "item one"})
        two_level_store.put(("ns", "x"), "k2", {"content": "item two"})

        results = two_level_store.search(("ns", "x"))
        assert len(results) >= 1

    def test_search_user_scoping(self, scoped_store):
        """Scoped store auto-adds user filter to searches."""
        scoped_store.put(("facts",), "f1", {"content": "alice fact"})
        results = scoped_store.search(("facts",), query="fact")
        assert len(results) > 0


# ── List namespaces tests ────────────────────────────────────────────

class TestListNamespaces:

    def test_list_namespaces_basic(self, two_level_store):
        """list_namespaces() returns stored namespace tuples."""
        two_level_store.put(("memories", "alice"), "f1", {"content": "test"})
        two_level_store.put(("facts", "bob"), "f2", {"content": "test"})

        namespaces = two_level_store.list_namespaces()
        assert ("memories", "alice") in namespaces
        assert ("facts", "bob") in namespaces

    def test_list_namespaces_deduplication(self, two_level_store):
        """Duplicate namespaces are returned once."""
        two_level_store.put(("ns", "user"), "k1", {"content": "one"})
        two_level_store.put(("ns", "user"), "k2", {"content": "two"})

        namespaces = two_level_store.list_namespaces()
        assert namespaces.count(("ns", "user")) == 1

    def test_list_namespaces_prefix_filter(self, two_level_store):
        """list_namespaces(prefix=...) filters results."""
        two_level_store.put(("memories", "alice"), "f1", {"content": "test"})
        two_level_store.put(("facts", "alice"), "f2", {"content": "test"})

        namespaces = two_level_store.list_namespaces(prefix=("memories",))
        assert ("memories", "alice") in namespaces
        assert ("facts", "alice") not in namespaces

    def test_list_namespaces_max_depth(self, two_level_store):
        """max_depth truncates namespace tuples."""
        two_level_store.put(("a", "b"), "k", {"content": "deep"})

        namespaces = two_level_store.list_namespaces(max_depth=1)
        assert ("a",) in namespaces
        assert ("a", "b") not in namespaces

    def test_list_namespaces_limit(self, two_level_store):
        """limit caps the number of results."""
        two_level_store.put(("ns1", "x"), "k", {"content": "test"})
        two_level_store.put(("ns2", "x"), "k", {"content": "test"})
        two_level_store.put(("ns3", "x"), "k", {"content": "test"})

        namespaces = two_level_store.list_namespaces(limit=2)
        assert len(namespaces) == 2

    def test_list_namespaces_empty(self, store):
        """Empty store returns empty list (system docs excluded)."""
        assert store.list_namespaces() == []


# ── Batch tests ──────────────────────────────────────────────────────

class TestBatch:

    def test_batch_mixed_ops(self, store):
        """batch() handles mixed operation types."""
        results = store.batch([
            PutOp(("ns",), "k1", {"content": "hello"}),
            PutOp(("ns",), "k2", {"content": "world"}),
            GetOp(("ns",), "k1"),
        ])

        assert len(results) == 3
        assert results[0] is None  # PutOp
        assert results[1] is None  # PutOp
        assert results[2] is not None  # GetOp
        assert results[2].value["content"] == "hello"

    def test_batch_put_then_search(self, store):
        """Search finds items from same batch."""
        store.batch([
            PutOp(("ns",), "k1", {"content": "alpha"}),
            PutOp(("ns",), "k2", {"content": "beta"}),
        ])
        results = store.batch([
            SearchOp(("ns",), query="alpha"),
        ])
        assert len(results) == 1
        assert len(results[0]) > 0

    def test_batch_delete(self, store):
        """PutOp with value=None deletes."""
        store.put(("ns",), "k", {"content": "temp"})
        store.batch([PutOp(("ns",), "k", None)])
        assert store.get(("ns",), "k") is None


# ── User scoping tests ──────────────────────────────────────────────

class TestUserScoping:

    def test_user_tag_added_on_put(self, scoped_store):
        """user_id adds user tag alongside namespace-provided tags."""
        # scoped_store has namespace_keys=["category"], user_id="alice"
        scoped_store.put(("facts",), "k", {"content": "test"})
        raw = scoped_store.keeper.get("facts/k")
        assert raw.tags.get("category") == "facts"
        assert raw.tags.get("user") == "alice"

    def test_no_user_tag_without_scope(self, store):
        """Without user_id, only namespace tags are set."""
        store.put(("bob",), "k", {"content": "test"})
        raw = store.keeper.get("bob/k")
        # Default namespace_keys=["user"], so namespace provides user=bob
        assert raw.tags.get("user") == "bob"



# ── Config-based namespace_keys ──────────────────────────────────────

class TestConfigNamespaceKeys:

    def test_reads_from_config(self, mock_providers, tmp_path):
        """KeepStore picks up namespace_keys from keep.toml config."""
        from keep.config import save_config, StoreConfig
        config = StoreConfig(path=tmp_path, namespace_keys=["scope", "tenant"])
        save_config(config)
        kp = Keeper(store_path=tmp_path)
        kp._get_embedding_provider()
        store = KeepStore(keeper=kp)
        # Should use config's keys, not default
        assert store._namespace_keys == ["scope", "tenant"]

    def test_explicit_overrides_config(self, mock_providers, tmp_path):
        """Explicit namespace_keys param overrides config."""
        from keep.config import save_config, StoreConfig
        config = StoreConfig(path=tmp_path, namespace_keys=["scope", "tenant"])
        save_config(config)
        kp = Keeper(store_path=tmp_path)
        kp._get_embedding_provider()
        store = KeepStore(keeper=kp, namespace_keys=["user"])
        assert store._namespace_keys == ["user"]

    def test_default_when_no_config(self, keeper):
        """Falls back to default ["user"] when config has no namespace_keys."""
        store = KeepStore(keeper=keeper)
        assert store._namespace_keys == ["user"]


# ── Content key tests ────────────────────────────────────────────────

class TestContentKey:

    def test_custom_content_key(self, keeper):
        """Custom content_key extracts from different field."""
        store = KeepStore(keeper=keeper, content_key="text")
        store.put(("ns",), "k", {"text": "hello world"})
        item = store.get(("ns",), "k")
        assert item.value["text"] == "hello world"

    def test_content_key_default(self, store):
        """Default content_key is 'content'."""
        store.put(("ns",), "k", {"content": "hello"})
        raw = store.keeper.get("ns/k")
        # The content becomes the Keep document's summary/text
        assert "hello" in raw.summary


# ── Index parameter tests ───────────────────────────────────────────

class TestIndex:

    def test_index_false_stores_but_minimal_embedding(self, store):
        """index=False stores the item with minimal content."""
        store.put(("ns",), "k", {"content": "secret"}, index=False)
        item = store.get(("ns",), "k")
        assert item is not None
        assert item.value["content"] == "k"  # summary is the key name

    def test_index_specific_fields(self, store):
        """index=['content'] embeds only the content field."""
        store.put(
            ("ns",), "k",
            {"content": "important", "meta": "ignored"},
            index=["content"],
        )
        item = store.get(("ns",), "k")
        assert "important" in item.value["content"]
        assert item.value["meta"] == "ignored"


# ── Async tests ──────────────────────────────────────────────────────

class TestAsync:

    @pytest.mark.asyncio
    async def test_aget(self, store):
        """Async get works."""
        store.put(("ns",), "k", {"content": "hello"})
        item = await store.aget(("ns",), "k")
        assert item.value["content"] == "hello"

    @pytest.mark.asyncio
    async def test_aput_aget(self, store):
        """Async put/get round-trip."""
        await store.aput(("ns",), "k", {"content": "async"})
        item = await store.aget(("ns",), "k")
        assert item.value["content"] == "async"

    @pytest.mark.asyncio
    async def test_asearch(self, store):
        """Async search works."""
        store.put(("ns",), "k", {"content": "findable"})
        results = await store.asearch(("ns",), query="findable")
        assert len(results) > 0
