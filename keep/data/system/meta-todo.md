---
tags:
  category: system
  context: meta
---
# .meta/todo — Open Loops

Open loops: unresolved commitments, requests, and blocked work.
Surface during `now` and `get` because untracked commitments
erode trust. These are things someone said they'd do, or asked
for, that aren't resolved yet.

## Injection

Meta docs are query patterns, not prompts. Each query line below (e.g., `act=commitment status=open`) matches items by tag. Matching items are surfaced during `keep now` and `keep get` as contextual information. Lines with trailing `=` (e.g., `project=`) match the viewed item's value for that tag, scoping results to the same project/topic.

Meta docs currently drive query-based surfacing, not LLM prompt construction.

act=commitment status=open
act=request status=open
act=offer status=open
status=blocked

project=
topic=
