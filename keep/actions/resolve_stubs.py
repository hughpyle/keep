from __future__ import annotations

"""Resolve wikilink stubs when a real note arrives in a vault.

When a new file:// item is created whose stem matches an existing
``_link_stem`` stub, this action rewrites references pointing at the
stub to point at the real item instead.
"""

import logging
from pathlib import PurePosixPath
from typing import Any

from ..types import file_uri_to_path
from . import action

logger = logging.getLogger(__name__)


def _file_stem(item_id: str) -> str | None:
    """Extract the bare filename stem from a file:// URI."""
    if not item_id.startswith("file://"):
        return None
    path = PurePosixPath(file_uri_to_path(item_id))
    return path.stem or None


@action(id="resolve_stubs", priority=1)
class ResolveStubs:
    """Rewrite wikilink stub references when the real note arrives."""

    def run(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        item_id = str(params.get("item_id", ""))
        if not item_id.startswith("file://"):
            return {"skipped": True, "reason": "not_file_uri"}

        stem = _file_stem(item_id)
        if not stem:
            return {"skipped": True, "reason": "no_stem"}

        # Find stubs with _link_stem matching this stem
        stubs = context.list_items(
            tags={"_link_stem": stem, "_source": "link"},
            include_hidden=True,
            limit=50,
        )
        if not isinstance(stubs, list) or not stubs:
            return {"skipped": True, "reason": "no_matching_stubs"}

        # Don't resolve stubs that point at ourselves
        stub_ids = [
            str(getattr(s, "id", ""))
            for s in stubs
            if str(getattr(s, "id", "")) != item_id
        ]
        if not stub_ids:
            return {"skipped": True, "reason": "self_only"}

        mutations: list[dict[str, Any]] = []
        rewritten_count = 0

        for stub_id in stub_ids:
            # Find items that reference this stub via the edges table
            find_ref = getattr(context, "find_referencing", None)
            if not callable(find_ref):
                continue
            referrers = find_ref(stub_id)
            if not referrers:
                continue

            for referrer in referrers:
                ref_id = str(getattr(referrer, "id", ""))
                ref_tags = dict(getattr(referrer, "tags", {}) or {})
                refs = ref_tags.get("references")
                if refs is None:
                    continue

                from ..types import parse_ref
                if isinstance(refs, str):
                    refs = [refs]
                if not isinstance(refs, list):
                    continue

                new_refs = []
                updated = False
                for r in refs:
                    rid, alias = parse_ref(r)
                    if rid == stub_id:
                        new_val = f"{item_id}[[{alias}]]" if alias else item_id
                        new_refs.append(new_val)
                        updated = True
                    else:
                        new_refs.append(r)

                if updated:
                    ref_tags["references"] = new_refs
                    mutations.append({
                        "op": "set_tags",
                        "target": ref_id,
                        "tags": ref_tags,
                    })
                    rewritten_count += 1

        if not mutations:
            return {"skipped": True, "reason": "no_referrers"}

        logger.info(
            "Resolved %d stub reference(s) for stem %r → %s",
            rewritten_count, stem, item_id,
        )
        return {
            "stubs_matched": len(stub_ids),
            "references_rewritten": rewritten_count,
            "mutations": mutations,
        }
