"""Work processor — claims and executes queued work items.

Replaces FlowEngine's process_requested_work_batch with direct
task_workflows dispatch.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Optional

from .work_queue import WorkQueue

if TYPE_CHECKING:
    from .api import Keeper

logger = logging.getLogger(__name__)


def process_work_batch(
    keeper: "Keeper",
    queue: WorkQueue,
    *,
    limit: int = 10,
    worker_id: Optional[str] = None,
    lease_seconds: int = 120,
) -> dict[str, Any]:
    """Claim and execute a batch of work items.

    Returns a summary dict compatible with the old FlowEngine interface.
    """
    worker = worker_id or f"local-daemon:{os.getpid()}"
    items = queue.claim(worker, limit=limit, lease_seconds=lease_seconds)

    stats: dict[str, int] = {"claimed": len(items), "processed": 0, "failed": 0, "dead_lettered": 0}
    errors: list[dict[str, str]] = []

    for item in items:
        # Skip if superseded by a newer item
        if (
            item.supersede_key
            and item.created_at
            and queue.has_superseding(item.work_id, item.supersede_key, item.created_at)
        ):
            queue.complete(item.work_id, {"status": "superseded"})
            stats["processed"] += 1
            continue

        try:
            outcome = _execute_work_item(keeper, item.kind, item.input)
            status = outcome.get("status", "applied")
            details = outcome.get("details")
            queue.complete(item.work_id, outcome)
            if status == "skipped":
                target = item.input.get("item_id") or item.input.get("id") or "?"
                logger.info("Work item %s (%s) skipped [%s]: %s", item.work_id, item.kind, target, details or {})
            stats["processed"] += 1
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            logger.warning("Work item %s (%s) failed: %s", item.work_id, item.kind, msg)
            queue.fail(item.work_id, worker, msg)
            stats["failed"] += 1
            errors.append({"work_id": item.work_id, "error": msg})

    stats["errors"] = errors  # type: ignore[assignment]

    # Log perf summary after each batch with actual work
    if stats["processed"] or stats["failed"]:
        from .perf_stats import perf
        perf.log_summary()

    return stats


def _execute_work_item(
    keeper: "Keeper",
    kind: str,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single work item via keeper._run_local_task_workflow.

    Returns the outcome dict (keys: status, details).
    """
    task_type = input_data.get("task_type") or kind
    item_id = str(input_data.get("item_id") or input_data.get("id") or "").strip()
    if not item_id:
        raise ValueError(f"work item missing item_id (kind={kind})")

    collection = str(
        input_data.get("collection")
        or input_data.get("doc_coll")
        or keeper._resolve_doc_collection()
    )
    content = str(input_data.get("content") or "")
    metadata = input_data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    outcome = keeper._run_local_task_workflow(
        task_type=task_type,
        item_id=item_id,
        collection=collection,
        content=content,
        metadata=metadata,
    )
    return outcome or {"status": "applied"}
