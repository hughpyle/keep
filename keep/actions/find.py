from __future__ import annotations

from typing import Any

from . import action, item_to_result


@action(id="find")
class Find:
    def run(self, params: dict[str, Any], context) -> dict[str, Any]:
        query = params.get("query")
        similar_to = params.get("similar_to")
        tags = params.get("tags") if isinstance(params.get("tags"), dict) else None
        prefix = params.get("prefix")
        since = params.get("since")
        until = params.get("until")
        offset = int(params.get("offset") or 0)
        include_hidden = bool(params.get("include_hidden", False))
        order_by = str(params.get("order_by") or "updated")
        limit = int(params.get("limit", 10))
        limit = max(limit, 1)

        # Bias: {id: weight} — negative suppresses/excludes, positive boosts
        bias = params.get("bias")
        if isinstance(bias, dict):
            bias = {str(k): float(v) for k, v in bias.items()
                    if isinstance(v, (int, float))}
        else:
            bias = None
        # Count how many items might be excluded (bias <= -1.0)
        n_excluded = sum(1 for v in (bias or {}).values() if v <= -1.0)

        has_selector = any([
            bool(query),
            bool(similar_to),
            bool(tags),
            bool(prefix),
            bool(since),
        ])
        if not has_selector:
            raise ValueError("find requires one of query, similar_to, tags, prefix, or since")
        if query and similar_to:
            raise ValueError("find.query and find.similar_to are mutually exclusive")

        fetch_limit = limit + n_excluded + offset

        if query or similar_to:
            rows = context.find(
                str(query) if query is not None else None,
                tags=tags,
                similar_to=str(similar_to) if similar_to is not None else None,
                limit=fetch_limit,
                since=str(since) if since is not None else None,
                until=str(until) if until is not None else None,
                include_hidden=include_hidden,
            )
        else:
            rows = context.list_items(
                prefix=str(prefix) if prefix is not None else None,
                tags=tags,
                since=str(since) if since is not None else None,
                until=str(until) if until is not None else None,
                order_by=order_by,
                include_hidden=include_hidden,
                limit=fetch_limit,
            )

        # Apply bias exclusions before converting to result dicts
        if bias:
            rows = [r for r in rows
                    if not (getattr(r, "id", None) in bias
                            and bias[getattr(r, "id", None)] <= -1.0)]

        # Apply offset
        if offset > 0:
            rows = rows[offset:]

        rows = rows[:limit]
        results = [item_to_result(row) for row in rows]

        # Apply bias score adjustments on result dicts (Items are frozen)
        if bias:
            for r in results:
                rid = r.get("id")
                if rid and rid in bias:
                    w = bias[rid]
                    if w > -1.0 and isinstance(r.get("score"), (int, float)):
                        r["score"] = r["score"] + w * 0.2
            results.sort(key=lambda r: -(r.get("score") or 0))
        return {
            "results": results,
            "count": len(results),
        }
