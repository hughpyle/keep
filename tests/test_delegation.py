"""Integration tests for task delegation in process_pending()."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from keep.pending_summaries import PendingSummaryQueue, PendingSummary
from keep.processors import ProcessorResult, DELEGATABLE_TASK_TYPES
from keep.task_client import TaskClient, TaskClientError


class TestDelegateTask:
    """Test _delegate_task method on Keeper."""

    def test_delegates_summarize_task(self, mock_providers, tmp_path):
        """Summarize tasks are delegated when TaskClient is available."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        # Set up a real pending queue for this test
        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        # Mock TaskClient
        mock_tc = MagicMock(spec=TaskClient)
        mock_tc.submit.return_value = "remote-task-001"
        kp._task_client = mock_tc

        # Enqueue and dequeue a summarize task
        queue.enqueue("doc1", "default", "Long content to summarize")
        items = queue.dequeue(limit=1)
        assert len(items) == 1

        # Call _delegate_task
        kp._delegate_task(items[0])

        # Should have submitted to remote
        mock_tc.submit.assert_called_once()
        call_args = mock_tc.submit.call_args
        assert call_args[0][0] == "summarize"
        assert call_args[0][1] == "Long content to summarize"

        # Should be marked as delegated
        delegated = queue.list_delegated()
        assert len(delegated) == 1
        assert delegated[0].metadata["_remote_task_id"] == "remote-task-001"

        queue.close()
        kp.close()

    def test_process_pending_delegates_instead_of_local(self, mock_providers, tmp_path):
        """process_pending delegates tasks when TaskClient is available."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        mock_tc = MagicMock(spec=TaskClient)
        mock_tc.submit.return_value = "remote-task-002"
        kp._task_client = mock_tc

        # Enqueue a summarize task
        queue.enqueue("doc1", "default", "content to summarize")

        result = kp.process_pending(limit=10)

        assert result["delegated"] == 1
        assert result["processed"] == 0  # Not processed locally

        queue.close()
        kp.close()

    def test_local_only_tasks_not_delegated(self, mock_providers, tmp_path):
        """embed/reindex tasks are never delegated."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        mock_tc = MagicMock(spec=TaskClient)
        kp._task_client = mock_tc

        # Enqueue an embed task (local-only)
        queue.enqueue("doc1", "default", "content", task_type="embed")

        result = kp.process_pending(limit=10)

        # embed should not be delegated
        mock_tc.submit.assert_not_called()
        assert result["delegated"] == 0

        queue.close()
        kp.close()

    def test_local_only_metadata_prevents_delegation(self, mock_providers, tmp_path):
        """_local_only metadata flag prevents delegation."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        mock_tc = MagicMock(spec=TaskClient)
        kp._task_client = mock_tc

        # Enqueue with _local_only flag
        queue.enqueue(
            "doc1", "default", "content",
            task_type="summarize",
            metadata={"_local_only": True},
        )

        result = kp.process_pending(limit=10)

        mock_tc.submit.assert_not_called()
        assert result["delegated"] == 0

        queue.close()
        kp.close()

    def test_fallback_to_local_on_delegation_error(self, mock_providers, tmp_path):
        """When delegation fails, falls back to local processing."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        mock_tc = MagicMock(spec=TaskClient)
        mock_tc.submit.side_effect = TaskClientError("Service unavailable")
        kp._task_client = mock_tc

        # Put a doc in the store so summarize can find it
        kp.put(content="This is test content for summarization", id="doc1")

        # The enqueue from put should create a summarize task
        # Clear and re-enqueue to control the content
        queue.clear()
        queue.enqueue("doc1", "default", "This is test content for summarization")

        result = kp.process_pending(limit=10)

        # Delegation failed, so it should fall back to local
        assert result["delegated"] == 0
        assert result["processed"] >= 1

        queue.close()
        kp.close()


class TestPollDelegated:
    """Test _poll_delegated method on Keeper."""

    def test_polls_and_applies_completed(self, mock_providers, tmp_path):
        """Completed delegated tasks are applied to the store."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        # Create a doc in store first
        kp.put(content="Original content", id="doc1")

        # Simulate a delegated task
        queue.enqueue("doc1", "default", "content to summarize")
        items = queue.dequeue(limit=1)
        queue.mark_delegated("doc1", "default", "summarize", "rt-001")

        mock_tc = MagicMock(spec=TaskClient)
        mock_tc.poll.return_value = {
            "status": "completed",
            "result": {"summary": "Remote summary result"},
            "error": None,
            "task_type": "summarize",
        }
        kp._task_client = mock_tc

        result = {"processed": 0, "failed": 0}
        kp._poll_delegated(result)

        assert result["processed"] == 1
        mock_tc.poll.assert_called_once_with("rt-001")
        mock_tc.acknowledge.assert_called_once_with("rt-001")

        # Task should be removed from queue
        assert queue.count_delegated() == 0

        queue.close()
        kp.close()

    def test_polls_failed_task(self, mock_providers, tmp_path):
        """Failed delegated tasks are returned to pending via fail()."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        queue.enqueue("doc1", "default", "content")
        queue.dequeue(limit=1)
        queue.mark_delegated("doc1", "default", "summarize", "rt-002")

        mock_tc = MagicMock(spec=TaskClient)
        mock_tc.poll.return_value = {
            "status": "failed",
            "result": None,
            "error": "Model crashed",
            "task_type": "summarize",
        }
        kp._task_client = mock_tc

        result = {"processed": 0, "failed": 0}
        kp._poll_delegated(result)

        assert result["failed"] == 1
        assert queue.count_delegated() == 0
        # Should be back in pending (via fail)
        assert queue.count() == 1

        queue.close()
        kp.close()

    def test_skips_still_processing(self, mock_providers, tmp_path):
        """Still-processing tasks are left alone."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        queue.enqueue("doc1", "default", "content")
        queue.dequeue(limit=1)
        queue.mark_delegated("doc1", "default", "summarize", "rt-003")

        mock_tc = MagicMock(spec=TaskClient)
        mock_tc.poll.return_value = {
            "status": "processing",
            "result": None,
            "error": None,
            "task_type": "summarize",
        }
        kp._task_client = mock_tc

        result = {"processed": 0, "failed": 0}
        kp._poll_delegated(result)

        assert result["processed"] == 0
        assert result["failed"] == 0
        # Still delegated
        assert queue.count_delegated() == 1

        queue.close()
        kp.close()

    def test_poll_error_skips_gracefully(self, mock_providers, tmp_path):
        """TaskClientError during poll doesn't crash the loop."""
        from keep import Keeper

        kp = Keeper(store_path=tmp_path)

        queue = PendingSummaryQueue(tmp_path / "pending.db")
        kp._pending_queue = queue

        queue.enqueue("doc1", "default", "content")
        queue.dequeue(limit=1)
        queue.mark_delegated("doc1", "default", "summarize", "rt-004")

        mock_tc = MagicMock(spec=TaskClient)
        mock_tc.poll.side_effect = TaskClientError("Network error")
        kp._task_client = mock_tc

        result = {"processed": 0, "failed": 0}
        kp._poll_delegated(result)

        # No crashes, task still delegated
        assert result["processed"] == 0
        assert result["failed"] == 0
        assert queue.count_delegated() == 1

        queue.close()
        kp.close()
