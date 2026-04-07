"""Unit tests for the MiniMax embedding provider.

The MiniMax embeddings API has several quirks worth exercising:
  * GroupId is a query parameter, not a header.
  * Errors come back inside ``base_resp.status_code`` even on HTTP 200.
  * The ``type`` field is asymmetric (``db`` for documents, ``query`` for searches).
"""

from unittest.mock import MagicMock, patch

import pytest

from keep.providers.base import EmbedTask
from keep.providers.embeddings import MiniMaxEmbedding


def _mock_session(response: MagicMock) -> MagicMock:
    session = MagicMock()
    session.post.return_value = response
    return session


def _ok_response(vectors: list[list[float]]) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.headers = {}
    r.json.return_value = {
        "vectors": vectors,
        "total_tokens": sum(len(v) for v in vectors),
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }
    r.raise_for_status.return_value = None
    return r


@pytest.fixture
def minimax_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-api-key")
    monkeypatch.setenv("MINIMAX_GROUP_ID", "test-group-id")


class TestConstruction:
    def test_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setenv("MINIMAX_GROUP_ID", "g")
        with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
            MiniMaxEmbedding()

    def test_requires_group_id(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "k")
        monkeypatch.delenv("MINIMAX_GROUP_ID", raising=False)
        with pytest.raises(ValueError, match="MINIMAX_GROUP_ID"):
            MiniMaxEmbedding()

    def test_known_dimension(self, minimax_env):
        e = MiniMaxEmbedding()
        assert e.dimension == 1536
        assert e.model_name == "embo-01"

    def test_unknown_model_lazy_dimension(self, minimax_env):
        """Unknown models must be accepted; dimension is detected lazily.

        This guarantees that future MiniMax embedding model names work
        without code changes — only ``keep.toml`` needs updating.
        """
        session = _mock_session(_ok_response([[0.1] * 768]))
        with patch("keep.providers.http.http_session", return_value=session):
            e = MiniMaxEmbedding(model="embo-future-768")
            assert e.model_name == "embo-future-768"
            assert e._dimension is None  # not in MODEL_DIMENSIONS
            assert e.dimension == 768  # detected from first embed
        _, kwargs = session.post.call_args
        assert kwargs["json"]["model"] == "embo-future-768"


class TestRequestShape:
    def test_embed_sends_db_type_and_group_id_query_param(self, minimax_env):
        session = _mock_session(_ok_response([[0.1, 0.2, 0.3]]))
        with patch("keep.providers.http.http_session", return_value=session):
            e = MiniMaxEmbedding()
            vec = e.embed("hello world", task=EmbedTask.DOCUMENT)

        assert vec == [0.1, 0.2, 0.3]
        session.post.assert_called_once()
        _, kwargs = session.post.call_args
        assert kwargs["params"] == {"GroupId": "test-group-id"}
        assert kwargs["headers"]["Authorization"] == "Bearer test-api-key"
        assert kwargs["json"] == {
            "model": "embo-01",
            "type": "db",
            "texts": ["hello world"],
        }

    def test_query_task_maps_to_query_type(self, minimax_env):
        session = _mock_session(_ok_response([[0.4, 0.5]]))
        with patch("keep.providers.http.http_session", return_value=session):
            e = MiniMaxEmbedding()
            e.embed("find me", task=EmbedTask.QUERY)

        _, kwargs = session.post.call_args
        assert kwargs["json"]["type"] == "query"

    def test_embed_batch_sends_all_texts(self, minimax_env):
        session = _mock_session(_ok_response([[1.0], [2.0], [3.0]]))
        with patch("keep.providers.http.http_session", return_value=session):
            e = MiniMaxEmbedding()
            out = e.embed_batch(["a", "b", "c"], task=EmbedTask.DOCUMENT)

        assert out == [[1.0], [2.0], [3.0]]
        _, kwargs = session.post.call_args
        assert kwargs["json"]["texts"] == ["a", "b", "c"]

    def test_embed_batch_empty_skips_request(self, minimax_env):
        session = _mock_session(_ok_response([]))
        with patch("keep.providers.http.http_session", return_value=session):
            e = MiniMaxEmbedding()
            assert e.embed_batch([]) == []
        session.post.assert_not_called()


class TestErrorHandling:
    def test_base_resp_nonzero_raises(self, minimax_env):
        bad = MagicMock()
        bad.status_code = 200
        bad.headers = {}
        bad.json.return_value = {
            "base_resp": {"status_code": 1008, "status_msg": "insufficient balance"},
        }
        bad.raise_for_status.return_value = None
        session = _mock_session(bad)
        with patch("keep.providers.http.http_session", return_value=session):
            e = MiniMaxEmbedding()
            with pytest.raises(RuntimeError, match="insufficient balance"):
                e.embed("hi")

    @pytest.mark.parametrize("status,msg", [
        (1004, "not authorized"),
        (2049, "invalid api key"),
    ])
    def test_base_resp_auth_error_fails_fast(self, minimax_env, status, msg):
        """1004 and 2049 must surface as auth errors without retry.

        2049 in particular is the failure mode when an international
        platform.minimax.io key is sent to api.minimax.chat (the legacy
        China host) — easy to hit if the wrong host is configured.
        """
        bad = MagicMock()
        bad.status_code = 200
        bad.headers = {}
        bad.json.return_value = {
            "base_resp": {"status_code": status, "status_msg": msg},
        }
        bad.raise_for_status.return_value = None
        session = _mock_session(bad)
        with patch("keep.providers.http.http_session", return_value=session):
            e = MiniMaxEmbedding()
            with pytest.raises(RuntimeError, match="authentication failed"):
                e.embed("hi")
        assert session.post.call_count == 1

    def test_base_resp_rate_limit_retries(self, minimax_env):
        """Status 1002 (RPM rate-limit) must retry with backoff, not fail fast."""
        rl = MagicMock()
        rl.status_code = 200
        rl.headers = {}
        rl.json.return_value = {
            "vectors": None,
            "base_resp": {"status_code": 1002, "status_msg": "rate limit exceeded(RPM)"},
        }
        rl.raise_for_status.return_value = None

        ok = _ok_response([[0.1, 0.2, 0.3]])

        session = MagicMock()
        # First call rate-limited, second succeeds.
        session.post.side_effect = [rl, ok]

        with patch("keep.providers.http.http_session", return_value=session):
            with patch("time.sleep"):  # don't actually wait
                e = MiniMaxEmbedding()
                vec = e.embed("hi")

        assert vec == [0.1, 0.2, 0.3]
        assert session.post.call_count == 2

    def test_http_401_fails_fast(self, minimax_env):
        bad = MagicMock()
        bad.status_code = 401
        bad.headers = {}
        session = _mock_session(bad)
        with patch("keep.providers.http.http_session", return_value=session):
            e = MiniMaxEmbedding()
            with pytest.raises(RuntimeError, match="authentication failed"):
                e.embed("hi")
        assert session.post.call_count == 1


class TestRegistration:
    def test_registered_in_global_registry(self):
        from keep.providers.base import get_registry
        assert "minimax" in get_registry().list_embedding_providers()
