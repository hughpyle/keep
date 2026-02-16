# LangChain / LangGraph Integration

Initial integration for using keep as memory within LangChain and LangGraph applications. Provides a LangGraph `BaseStore`, LangChain tools, a retriever, and middleware.

## Installation

```bash
pip install keep-skill[langchain]
```

Or install the discovery shim (pulls in everything):
```bash
pip install langchain-keep
```

You still need an embedding/summarization provider configured — see [QUICKSTART.md](QUICKSTART.md).

## Components

### KeepStore — LangGraph BaseStore

Maps LangGraph's namespace/key model to Keep's document model:

```python
from keep.langchain import KeepStore

store = KeepStore()                    # default store (~/.keep)
store = KeepStore(store="~/.keep")     # explicit path
store = KeepStore(keeper=my_keeper)    # existing Keeper instance
```

Use with LangGraph:

```python
from langgraph.graph import StateGraph

graph = StateGraph(...)
graph.compile(store=store)
```

Use with [langmem](https://github.com/langchain-ai/langmem):

```python
from langmem import create_manage_memory_tool, create_search_memory_tool

tools = [
    create_manage_memory_tool(namespace=("memories", "{user_id}")),
    create_search_memory_tool(namespace=("memories", "{user_id}")),
]
```

#### Namespace-to-tag mapping

LangGraph uses hierarchical namespace tuples like `("memories", "alice")`. Keep uses flat key-value tags. The `namespace_keys` setting bridges these:

```python
# Default: namespace_keys=["user"]
# ("alice",) → {"user": "alice"}

# Custom mapping:
store = KeepStore(namespace_keys=["category", "user"])
# ("memories", "alice") → {"category": "memories", "user": "alice"}
```

These become regular Keep tags — visible to CLI, searchable, filterable:

```bash
keep list --tag user=alice            # Find LangGraph-managed items
keep find "auth" -t category=memories # Search within a namespace
```

Configure in `keep.toml` instead of code:

```toml
[tags]
namespace_keys = ["category", "user"]
```

#### Value mapping

- `value["content"]` becomes Keep's document text (configurable via `content_key`)
- Other string values become tags
- Non-string values are preserved in a `_keep_data` system tag (JSON)
- The `_source=langchain` system tag marks KeepStore-managed items

### KeepNotesToolkit — LangChain Tools

Four curated tools for LangChain agents:

```python
from keep.langchain import KeepNotesToolkit
from keep import Keeper

toolkit = KeepNotesToolkit(keeper=Keeper())
tools = toolkit.get_tools()
# → [remember_note, recall_notes, get_context, update_context]
```

| Tool | Description |
|------|-------------|
| `remember_note` | Store a note with tags |
| `recall_notes` | Semantic search |
| `get_context` | Get current intentions (`keep now`) |
| `update_context` | Update current intentions |

### KeepNotesRetriever — BaseRetriever

For RAG chains with optional now-context injection:

```python
from keep.langchain import KeepNotesRetriever
from keep import Keeper

retriever = KeepNotesRetriever(keeper=Keeper(), k=5)
docs = retriever.invoke("authentication patterns")
```

With now-context (prepends current intentions):

```python
retriever = KeepNotesRetriever(keeper=Keeper(), include_now=True)
```

### KeepNotesMiddleware — LCEL Runnable

Auto-injects memory context into `RunnableConfig`:

```python
from keep.langchain import KeepNotesMiddleware
from keep import Keeper

middleware = KeepNotesMiddleware(keeper=Keeper())
chain = middleware | your_chain
```

## Multi-user scoping

For multi-user applications, use `user_id` to scope all operations:

```python
store = KeepStore(user_id="alice")
# All put/search/list operations auto-filter by user=alice
```

Combined with `required_tags` in config, this enforces per-user isolation:

```toml
[tags]
required = ["user"]
```

With this config, `put()` calls without a `user` tag raise `ValueError`. Scoped `set_now(scope="alice")` auto-tags `user=alice`, satisfying the requirement.

## Limitations

This is an initial integration. Known limitations:

- `list_namespaces()` scans up to 10K items (sufficient for most use cases)
- Search `filter` supports exact-match equality only (operator filters like `$gt` are not supported)
- KeepStore operations are synchronous; `abatch()` wraps the sync implementation
- The `langchain-keep` shim re-exports from `keep.langchain` — no additional functionality

## See Also

- [PYTHON-API.md](PYTHON-API.md) — Core Python API
- [TAGGING.md](TAGGING.md) — Tag system and conventions
- [KEEP-CONFIG.md](KEEP-CONFIG.md) — Configuration reference
- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture
