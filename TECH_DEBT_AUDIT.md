# Tech Debt Audit — keep

Generated: 2026-04-27 — keep-skill v0.141.1 — main @ 2668550

Scope: 114 Python files / ~54k LOC under `keep/` plus 91 test files / ~40k LOC under `tests/`. The OpenClaw and claude-code plugin packages were not audited in depth.

---

## Executive summary

1. **God files dominate**: `api.py` (6,805 LOC, 141 methods, highest churn at 280 commits / 6 mo) and `document_store.py` (4,601 LOC) carry most of the system. This is the dominant maintainability risk and the place hard bugs hide.
2. **CLI is no longer thin**. `docs/ARCHITECTURE.md:85-89` claims `cli_app.py` is a thin wrapper; it's 2,435 LOC and routinely instantiates a local `Keeper`. Either the docs lie or the architectural intent has drifted; both are debt.
3. **Inline imports are everywhere in `cli_app.py`** despite an explicit project rule (`CLAUDE.md` memory: "imports at module level"). Verified ~20 sites including `cli_app.py:154,815,848,853,1144,1145,1331,1332,1686,1724,1725,1732,1733,1737,1772,1776,1890–1892`. There's also `import logging` inline at `api.py:6768`. This is policy violation, not just style.
4. **Dual HTTP client**: `httpx>=0.27` and `requests>=2.33` are both top-level deps. `requests` is used in `providers/documents.py`, `providers/http.py`, `providers/ollama_utils.py`, `actions/assess_virustotal.py`; `httpx` in `remote.py`, `task_client.py`. Two retry semantics, two error hierarchies, two timeout defaults — and a wholly unnecessary dep.
5. **Untyped HTTP boundary**: `daemon_server.py:266 _read_body()` returns `dict` with no schema; handlers index `body.get("uri")`, `body.get("tags")` directly. Any malformed JSON crashes inside `Keeper` instead of returning 400.
6. **Silent error suppression in shutdown**: `api.py:6705–6764` has eight `except Exception: pass` blocks during `close()`. Cleanup-time silencing is defensible, but with zero logging (not even `logger.debug`) you cannot tell whether GPU memory was released or whether a store failed to flush.
7. **`test_review_fixes.py` is a 1,152-line regression graveyard** with 19 unrelated test classes (`TestTagQueries`, `TestSSRF`, `TestMissingEmbedding`, …). It's where coverage goes to die — easy to add to, impossible to navigate.
8. **`dict[str, Any]` everywhere**: 551 occurrences across `keep/`. The trust boundaries (daemon HTTP, MCP, flow params) are the egregious cases — `Item` and `ItemContext` types exist but aren't enforced at the boundary. Refactors will silently break callers.
9. **`document_store._migrate_schema` is a single 400+-line monolith** with `if current_version < 1: …; if current_version < 2: …` chains. Schema 14 in one block is unmaintainable; per-version migration methods are the standard idiom.
10. **Loose dependency pins**: `chromadb>=0.4`, `httpx>=0.27`, `requests>=2.33` etc. with no upper bounds. `chromadb` in particular has had multiple breaking minors. `uv.lock` pins versions, but a `>=` floor without a ceiling means new contributor environments can install incompatible majors.

---

## Architectural mental model

Surface clients (CLI, MCP, LangChain, Claude Desktop bundle) talk to a long-running daemon (`daemon.py` / `daemon_server.py`) over HTTP. The daemon hosts a `Keeper` (`api.py`, composed from four mixins: `_provider_lifecycle.py`, `_background_processing.py`, `_search_augmentation.py`, `_context_resolution.py`). The Keeper's stable execution boundary is `run_flow(state, params)` over named state-docs (CEL-evaluated YAML rules under `.state/*`); most user-visible operations are dispatched as flows that call into `actions/`. Storage is pluggable: local backends use SQLite (`document_store.py`) for documents/tags/parts/versions and ChromaDB (`store.py`) for vectors, with a separate pending-work queue (`pending_summaries.py`) and a continuation queue (`work_queue.py`). Providers (`providers/`) cover embeddings, summarization, document fetch, OCR, and media description, registered lazily.

Two architectural drifts are visible vs. the intent in `docs/ARCHITECTURE.md`:
- The thin-wrapper invariant for `cli_app.py` is violated (export, import, watch, doctor, setup all hold their own logic — 2,435 LOC).
- The "Hooks are responsible for harness-specific functionality" rule (`AGENTS.md`) is honored in MCP and the plugins, but the CLI module mixes daemon delegation with local Keeper construction (`cli_app.py:1733`, `cli_app.py:2382`).

Neither drift is wrong by itself, but neither is acknowledged in the docs.

---

## Findings

| ID | Category | File:Line | Severity | Effort | Description | Recommendation |
|----|----------|-----------|----------|--------|-------------|----------------|
| F001 | Architectural decay | `keep/api.py:148` (class `Keeper`) | High | L | 141 methods, 6,805 LOC across class + 4 mixins; highest churn file in repo (280 commits / 6 mo). Local reasoning is hard. | Don't rewrite. Extract two policy objects: a `_BodyAuthority` (currently `_resolve_note_body_summary` and friends) and a `_SearchContext` builder (deep-follow + RRF currently inline in `_search_augmentation.py`). |
| F002 | Architectural decay | `keep/document_store.py:327` (`_migrate_schema`) | High | M | Single 400+-line method handling all 14 schema versions in one `if current_version < N` chain. Adding v15 means re-reading v1–v14. | Split into `_migrate_v0_to_v1`, …, `_migrate_v13_to_v14` and dispatch via a registry. Same code, navigable. |
| F003 | Architectural decay | `docs/ARCHITECTURE.md:85-89` vs `keep/cli_app.py` (2,435 LOC) | High | M | Architecture doc says CLI is a "thin wrapper". `cli_app.py:1733` and `:2382` instantiate `Keeper()` directly; `_get_export_host` constructs a local Keeper for export. | Either update docs to match reality ("CLI delegates queries to daemon but owns export/import/watch locally") or migrate export/import to flow handlers. The current state confuses readers. |
| F004 | Consistency rot | `pyproject.toml:29,50` | High | M | Both `requests>=2.33` and `httpx>=0.27` are runtime deps. `requests` used in `providers/documents.py:18`, `providers/http.py:3`, `providers/ollama_utils.py:8`, `actions/assess_virustotal.py:27`, plus inline at `providers/embeddings.py:428`. `httpx` used in `remote.py:15`, `task_client.py:17`. | Pick one. `httpx` is async-ready and already used for the network-facing client; migrate the four `requests` callers. Removes ~500KB of deps and one error-handling style. |
| F005 | Consistency rot | `keep/cli_app.py:154,815,848,853,1102,1144,1145,1331,1332,1686,1724,1725,1732,1733,1737,1772,1776,1890,1891,1892` | High | S | ~20 inline `from .X import Y` inside function bodies. `CLAUDE.md` memory explicitly forbids this ("imports at module level"). | Hoist all to the top. If a circular import emerges, that itself is a finding to fix, not work around. Single PR. |
| F006 | Consistency rot | `keep/api.py:6768` | Medium | S | `import logging` inline inside `close()`. Same policy violation as F005. | One-line fix: hoist to top of file. |
| F007 | Type & contract | `keep/daemon_server.py:266 (_read_body)`, `:412 (_handle_get)`, `:461 (_handle_put)` | Critical | L | HTTP handlers accept arbitrary JSON, then index with `body.get("uri")` etc. without validation. Wrong types crash inside `Keeper`, surfacing as 500. | Define Pydantic models per route (e.g. `PutRequest`, `TagRequest`); `pydantic.ValidationError` → 400. The codebase already uses Pydantic via MCP — pattern exists. |
| F008 | Type & contract | `keep/` (551 occurrences) | High | L | `dict[str, Any]` is the dominant shape at every boundary: flow params (`state_doc_runtime.py:44,58,62`), remote client (`remote.py:90,113`), daemon responses (`daemon_server.py:244`). Refactors break silently. | Start at the boundaries: TypedDicts for `FlowParams`, `DaemonResponse`, `ItemPayload`. Don't try to type the runtime internals. |
| F009 | Type & contract | `keep/api.py` (~70 methods) | Medium | M | Many `Keeper` methods lack return annotations (`api.py:121,403,491,1292,1770`). Mixin methods especially. Highest-churn file in the repo, hardest to refactor safely. | Add return types incrementally. Mypy/pyright in CI would lock progress in. Not required to be `--strict`. |
| F010 | Test debt | `tests/test_review_fixes.py` (1,152 LOC) | High | M | 19 test classes covering unrelated bugs (`TestTagQueries`, `TestSSRF`, `TestMissingEmbedding`, …). No organizing principle. | Migrate each class to its proper home: `TestTagQueries` → `test_document_store.py`, `TestSSRF` → `test_daemon_server.py`. Empty file becomes a placeholder for the next regression bin. |
| F011 | Error handling | `keep/api.py:6705,6711,6718,6726,6734,6741,6748,6755,6763` | High | S | Eight `try/.../except Exception: pass` blocks inside `close()`. Cleanup-time silence is defensible, but zero logging means you can't diagnose a stuck GPU lock or a hung SQLite handle from logs alone. | Add `logger.debug("close: failed to release X", exc_info=True)` to each. Better: introduce `_safe_close(self, attr)` that does this once; the eight blocks collapse to one-liners. |
| F012 | Error handling | `keep/api.py:927-928` | Medium | S | Post-reindex `save_config()` wrapped in `except Exception: pass`. A corrupted/unwritable config goes silent until the next start fails. | Log at WARNING. If config is truly best-effort here, comment why. |
| F013 | Error handling | `keep/console_support.py:980-983` | High | S | `_render_context_if_item()` swallows all exceptions when calling `kp.get_context()` and falls through to render a stub. The user gets a context-less response and never sees the error. | Log at INFO with the item id; consider a synthetic `_context_error` tag on the fallback Item so the breakdown is visible to the operator. |
| F014 | Error handling | `keep/daemon_server.py:220-226` | Medium | S | Generic 500 response on `Exception` returns `{"error": "internal server error"}` with no request ID. Logs and client are decorrelated. | Generate a uuid, include in both `logger.warning` and the 500 body. Trivial change, big debugging win. |
| F015 | Performance | `keep/document_store.py:124-125` | Medium | M | All SQLite calls go through a single `RLock`, including reads. WAL mode would let multiple readers proceed concurrently; the lock prevents that. | Drop the lock for SELECTs (sqlite3 + WAL is reader-safe). Keep it for writes. Verify with concurrent test. |
| F016 | Performance | `keep/daemon_server.py:843-860` (`ThreadingHTTPServer`) | Medium | L | Sync HTTP server. One blocked request (slow embedding, big analyze) holds a thread; ~10 concurrent slow requests can starve the pool. | Acceptable for current single-user CLI/MCP load. Document the concurrency budget. Async server (FastAPI/uvicorn) is a future swap, not a debt-now item. |
| F017 | Observability | `keep/document_store.py`, `keep/store.py` | Medium | M | OpenTelemetry is wired (`tracing.py`) and used in `analyzers.py:481+`, but `document_store._execute()` and `ChromaStore.query()` — the two slowest paths — have no spans. Latency attribution in production is impossible. | Add spans around `_execute` and `ChromaStore.query` with attributes (`row_count`, `query_type`). One PR. |
| F018 | Observability | `keep/api.py` `put`/`find`/`get` (lines 3808, 4497, 4698) | Medium | S | High-level entry points don't log invocation. Only low-level stores do. Correlating user intent → DB activity requires guessing. | One `logger.debug("put: id=%s force=%s", id, force)` per entry. Trace context via OTel already, just no app-level breadcrumb. |
| F019 | Security | `keep/markdown_export.py:76,991-994` (`_id_to_rel_path`, `write`) | Medium | M | `_id_to_rel_path()` encodes per-segment, then writes `out_dir / rel_path`. After encoding, segments still resolve via `..` if attacker controls the ID. | After joining, call `(out_dir / rel_path).resolve().relative_to(out_dir.resolve())`; raise on `ValueError`. Final-path check, not per-segment. |
| F020 | Security | `keep/paths.py:104-120` (`validate_path_within_home`) | Medium | M | Boundary is `$HOME`. On a multi-user system, `keep put /home/alice/...` from bob is "valid". Permission errors will block the read but the intent is wrong. | Tighten to "owned by current uid" (use `Path.stat().st_uid == os.geteuid()`) for daemon use; CLI may be looser by design. |
| F021 | Security | `keep/providers/documents.py:209-230` (file:// resolve) | Medium | S | `path.resolve()` follows symlinks; a symlink in `$HOME` to `/etc/passwd` is silently followed. The home check passes after resolution. | Detect symlinks via `Path.is_symlink()` on the original path (or any parent) and at least log a warning. Optional: refuse with `--no-follow-symlinks`. |
| F022 | Security | `keep/daemon_server.py:174-194` | Low | M | Bearer auth with no rate limiting. Loopback-only is the default and safe; remote bind (`--bind`) is documented but the brute-force surface is unmitigated. | In-memory failed-attempt counter per remote IP; 10 fails → 30s lockout. Or document explicitly that remote bind requires fronting with a rate-limiting proxy. |
| F023 | Documentation | `docs/ARCHITECTURE.md:85-89` | High | S | Claims `cli_app.py` is a thin Typer wrapper; reality is 2,435 LOC. See F003. | Update doc to match (and call out the local-only commands explicitly), or migrate the local logic. Pick one. |
| F024 | Documentation | `docs/KEEP-CONFIG.md` vs `keep/config.py` (1,152 LOC) | Medium | M | Config dataclass exposes ~25 options; not all are documented. No automated sync. | A `keep config --doc` command that emits the dataclass field names + descriptions, plus a CI check that the doc enumerates each. |
| F025 | Documentation | repo-wide env vars (~40+) | Medium | S | `KEEP_VERBOSE`, `KEEP_LOCAL_ONLY`, `KEEP_DAEMON_TRUSTED_PROXY`, `KEEP_TRACE`, `OLLAMA_HOST`, etc. read at `config.py:348,557-573`, `logging_config.py:13`, `console_support.py:1623-1628`, `mcp.py:61`. Not all documented. | Single-source registry (`keep/env.py`) with names, defaults, descriptions; surface in `keep config --env`. |
| F026 | Dependencies | `pyproject.toml:27-59` | Medium | S | All deps are `>=X` with no upper bound: `chromadb>=0.4`, `httpx>=0.27`, `requests>=2.33`, `pypdf>=6.10.0`, … | Add upper bounds for libraries that have shipped breaking minors: `chromadb<1.0`, `httpx<1.0`, `mcp<2.0`. uv.lock pins concrete versions, but new contributors install from `>=` floors. |
| F027 | Dependencies | `pyproject.toml:79-82` | Low | S | `langchain-core>=1.0.0,<2.0.0` — already capped. Counter-example to F026, showing the team knows how. Just inconsistent. | Apply the same pattern to other top-level pins. |
| F028 | Architectural decay | `keep/cli_app.py:260-272` vs `keep/daemon_client.py:45-93` | Medium | S | Both implement daemon discovery + retry on disconnect, with slightly different semantics (cli_app re-resolves port; daemon_client retries 2x). Two ways, neither clearly canonical. | Consolidate into `daemon_client.http_request`; delete `_daemon_request`. The duplication is one of the larger churn risks (both files are touched often). |
| F029 | Architectural decay | `keep/cli_app.py:156, 1147, 1333, 1902` | Low | S | Pattern `Path(_global_store).resolve() if _global_store else get_config_dir()` repeats 4×. `daemon_client.resolve_store_path` already exists. | Replace duplicates with the helper. 4-line PR. |
| F030 | Architectural decay | `keep/api.py:2816` (`_resolve_note_body_summary`) | Medium | S | 9 parameters; mutates `merged_tags` in place. The mutation is implicit. | Wrap context in a `_PutContext` dataclass; mutation becomes `ctx.merged_tags[…]` which is at least visible. |
| F031 | Architectural decay | `keep/api.py:4195-4210` | Low | M | 10-level nesting in deep-group injection logic. Hard to follow during the highest-churn file's frequent edits. | Extract `_inject_deep_groups(items, deep_groups, doc_coll)`. |
| F032 | Architectural decay | `keep/api.py:5615-5650` | Low | S | Three nested conditionals ("parts current", "incremental vstring", "single-version skip") share one blob. | Three guard helpers: `_parts_already_current`, `_is_incremental_vstring`, `_should_skip_single_version`. |
| F033 | Test debt | `tests/test_processors.py:197`, `tests/test_ocr.py:88,110,132,157,181,187,211,216,248` | Low | S | Bare `pytest.skip()` when optional deps absent, no reason string. | Use `pytest.importorskip("pypdf")` or `@pytest.mark.skipif` with a reason. |
| F034 | Test debt | `tests/test_assess_virustotal.py:53` | Low | S | Mocks `requests.get` but no `e2e` test exists for the real VT API. Contract drift will go undetected. | Add an `@pytest.mark.e2e` test gated on `VIRUSTOTAL_API_KEY`. CI runs it on schedule, not per-PR. |
| F035 | Test debt | `tests/conftest.py` (1,548 LOC) | Medium | M | Conftest is the third-largest file in `tests/`. That much fixture machinery is itself a source of bugs and slow startup. | Audit which fixtures are actually shared vs. should be local to a single test file. Likely halves it. |
| F036 | Performance | `keep/pending_summaries.py:270-289` | Low | S | `_recover_stale_claims` loops one UPDATE per task type. Currently 2 types. | Single UPDATE with `CASE WHEN task_type = ? THEN datetime(...)`. Negligible win today; future-proofing. |
| F037 | Performance | `keep/pending_summaries.py:353-424` | Medium | M | `dequeue` calls `_recover_stale_claims` under the main `BEGIN IMMEDIATE` lock. A large stale batch blocks all enqueuers. | Run recovery on a separate cadence (timer thread) or in a separate transaction outside `_lock`. |
| F038 | Consistency rot | `keep/providers/embeddings.py:52`, `keep/providers/mlx.py:62,150,244,345`, `keep/providers/ollama_utils.py:60,94,98,103`, `keep/daemon_client.py:145,259` | Low | M | `print(..., file=sys.stderr)` for status alongside `logger.info` elsewhere. Bypasses log filters and structured fields. | Migrate to `logger.info`. Keep a `--quiet` flag if user-facing progress is needed. |
| F039 | Consistency rot | `keep/daemon_server.py:244-245` | Low | S | `json.dumps(default=str)` quietly stringifies anything non-JSON (datetimes, Enums, dataclasses). Hides type bugs. | Custom encoder in a shared module; explicit `to_dict()` on dataclasses. |
| F040 | Documentation | `keep/api.py`, `keep/store.py`, `keep/document_store.py` (internal helpers) | Low | M | Public methods are documented. Internal helpers like `_find_direct`, `_apply_recency_decay`, `_resolve_note_body_summary` are not. They're the ones that change. | Docstring on every method touched in the next 5 PRs. Don't try to backfill all at once. |
| F041 | Architectural decay | `keep/console_support.py` (2,522 LOC) | Medium | M | Mixes rendering, doctor diagnostics, daemon control wrappers, and `_get_keeper`. Three concerns, one file. | Split into `console_render.py` (render_context et al), `console_doctor.py`, `console_keeper.py`. Mostly mechanical. |
| F042 | Architectural decay | `keep/_background_processing.py` (1,741 LOC, mixin) | Medium | L | Already a mixin extraction, but at 1.7k LOC it's the biggest of the four. Likely contains internal seams. | Investigate whether process spawning, work dispatch, and pipeline glue are separable. Don't force it if not. |
| F043 | Test debt | `tests/test_data_export.py` (2,325 LOC) | Low | M | Largest test file. Likely covers many scenarios that could live closer to their feature tests. | Audit overlap with `test_markdown_mirrors.py` (1,172 LOC). The two together cover the same export surface. |
| F044 | Security | `keep/daemon_server.py` (CORS / Origin) | Low | M | No CORS headers / Origin validation. For loopback-only this is fine; for `--bind` deployments it matters. | Configurable Origin allowlist when bind is non-loopback; refuse when missing. |
| F045 | Architectural decay | `keep/cli_app.py:2105-2160` | Low | S | Branching `_supports_local_markdown_export(kp)` → call local vs. delegate to daemon, with nested try/except. Selection logic in the CLI. | Extract `_export_markdown(kp, …)` helper that picks the strategy. |

---

## Top 5 — if you fix nothing else, fix these

### 1. F007: Validate inputs at the daemon HTTP boundary
The daemon trusts JSON shapes. Wrong types crash inside `Keeper` and surface as opaque 500s. This is one Pydantic model per route — the codebase already uses Pydantic via MCP, so the pattern is in-house.

```python
# keep/daemon_server.py
class PutRequest(BaseModel):
    id: str | None = None
    uri: str | None = None
    content: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    force: bool = False

def _handle_put(self, groups: dict):
    try:
        req = PutRequest.model_validate(self._read_body())
    except ValidationError as e:
        return self._json(400, {"error": "validation", "detail": e.errors()})
    # … existing logic on req.id, req.uri, req.tags
```

### 2. F005 + F006: Hoist inline imports to the top of `cli_app.py` and `api.py`
Single PR. ~25 sites. The repo policy is explicit; the lint should reject these. Add a ruff rule (`PLC0415`) so future violations don't ship.

### 3. F011: Replace the 8 silent excepts in `Keeper.close()` with one helper
Cleanup silence is fine; cleanup blindness is not. `_safe_close(self, attr)` that logs at debug, used eight times, replaces all the boilerplate and turns shutdown into a traceable event.

### 4. F002: Split `_migrate_schema` into per-version methods
A 400-line nested-if migration block is unmaintainable. One method per version, dispatched by a registry. Schema 14 is the hint — 15 will hurt.

### 5. F010: Drain `test_review_fixes.py` into proper test homes
1,152 lines of regression bin is the strongest signal in the test suite that "we patched a thing once". Move each class to where it belongs. The empty file becomes the next migration's holding pen.

---

## Quick wins (low effort × medium+ severity)

- [ ] F005: Hoist inline imports in `cli_app.py` (S, High)
- [ ] F006: Hoist `import logging` in `api.py:6768` (S, Medium)
- [ ] F011: `_safe_close` helper for shutdown silencing (S, High)
- [ ] F012: Log the `save_config` failure at `api.py:927-928` (S, Medium)
- [ ] F013: Log the silent context render failure at `console_support.py:982` (S, High)
- [ ] F014: Add request-id to daemon 500 responses (S, Medium)
- [ ] F018: Add entry-log to `Keeper.put/find/get` (S, Medium)
- [ ] F029: Replace 4× repeated store-path resolution with `resolve_store_path()` (S, Low)
- [ ] F033: Replace bare `pytest.skip` with `importorskip` / `skipif` (S, Low)
- [ ] F034: Add `@pytest.mark.e2e` VirusTotal test (S, Low)
- [ ] F036: Single-UPDATE for stale claim recovery (S, Low)
- [ ] F045: Extract `_export_markdown` strategy helper (S, Low)
- [ ] F026: Add upper bounds on `chromadb`, `httpx`, `mcp` pins (S, Medium)

---

## Things that look bad but are actually fine

These were flagged by the audit subagents and discarded after verification. Listing them so the rationale survives.

- **`api.py:148` Keeper-with-mixins is not a god class.** It composes four mixins by responsibility (provider lifecycle, background processing, search augmentation, context resolution). The 141-method count is real, but the mixin split is documented and intentional. The right intervention is internal factoring (F030, F031), not breaking up the class.
- **F-string SQL in `document_store.py:141,205,207,1083,1626,2000,2004,2265,2455,2618`.** All variable interpolation is from internal constants (`SCHEMA_VERSION`, table/trigger names managed by code) or whitelisted identifiers (e.g. `order_col, order_dir` validated against an allowlist at `document_store.py:2615-2617`). User-controlled values still go through `?` placeholders. Not an injection risk.
- **File-descriptor leak in `keep/_background_processing.py:1697`.** Audit agent flagged unclosed `log_fd`. Verified: `_background_processing.py:1739-1740` closes it after Popen. False alarm.
- **Embedding cache unbounded growth (`providers/embedding_cache.py`).** Agent claimed eviction missing. Verified at lines 167–192: `_maybe_evict()` runs on every write, deleting 10% by `last_accessed` when over `max_entries`. Working as designed.
- **`except Exception: pass` in `Keeper.close()`** — pattern is correct for resource cleanup (failure of one release shouldn't block the others). The fix (F011) is to add logging, not to remove the pattern.
- **TOCTOU in `_is_private_url()` (`providers/documents.py:1259-1292`).** DNS-resolution-time-of-check is a known limitation; the code comment at `:1262-1263` acknowledges it explicitly and notes "sufficient for CLI use; hosted services should enforce at network layer." Documented limitation, acceptable tradeoff.
- **ChromaDB telemetry disabled at `keep/store.py:74-83`.** Looks like a workaround. It is — for a real ChromaDB shutdown deadlock. Comment explains. Leave alone.
- **`ThreadingHTTPServer` in the daemon (F016).** Worth flagging but not worth fixing now. Designed for a single user with 1–5 concurrent requests; async would be a refactor with a real return only at multi-tenant scale.
- **Path traversal via `_id_to_rel_path` (F019)** — this one IS a finding because the validator runs per-segment, not on the joined path. But the actual risk is bounded by the user controlling their own IDs (single-user system). High severity in a hosted multi-tenant context, lower in single-user. Listed as Medium in the table for that reason.
- **Inline imports for genuinely conditional dependencies** (e.g., `subprocess` in a Linux-only branch, `mlx` in a macOS-only branch). The blanket inline-import ban (F005) targets unconditional deps; conditional ones may stay inline with a comment saying why.
- **141 methods on `Keeper`** is high but every public surface method ultimately routes through `flow_client` to `run_flow`. That's the stable boundary. Reducing the public surface doesn't reduce the actual contract.

---

## Open questions for the maintainer

1. **Is `cli_app.py:1733` Keeper-instantiation intentional?** `_global_store` plus a local `Keeper()` is constructed inside CLI commands like `daemon` and `import`. Architecture says "thin wrapper". Either (a) update the doc to explicitly enumerate local commands or (b) migrate these to flow handlers. Which?
2. **Is `requests` being kept for a reason?** OpenRouter / Voyage-style providers may have idiosyncratic SDKs that lean on requests internally; `httpx`-only might be impossible. If so, document it and remove the rest.
3. **`test_review_fixes.py` — is this an intentional regression bin or just where things drift?** Some teams keep one file like this on purpose. If yes, document the convention; if no, drain it.
4. **The four mixins on `Keeper`** — what's the reason for using inheritance rather than composition? Composition would let the daemon hold a `Keeper` with explicit `keeper.search`, `keeper.providers` namespaces. Mixins make `dir(keeper)` impossible to reason about.
5. **Is the `--bind` (non-loopback) daemon mode actually used in production anywhere?** Several Medium findings (F022, F044) only matter if it is. If it's purely theoretical, the docs should call it out as "use behind a reverse proxy that handles auth".
6. **Remote `Keeper` (`remote.py`) uses `httpx` and bypasses the local `flow_client`/`Keeper` paths.** Is the public surface of `RemoteKeeper` formally compatible with `KeeperProtocol`, or just close-enough? Compatibility tests would surface drift early.
7. **Why is `_context_resolution.py` 1,192 LOC?** It started as an extraction; it's now ⅓ the size of the parent. Has it grown new responsibilities? Worth a focused look.

---

*Generated by `/tech-debt-audit`. Findings cite `file:line` so they can be re-checked. The verification I did caught two false positives (the FD-leak and the missing-eviction claims), so callers should still spot-check before acting on individual findings.*
