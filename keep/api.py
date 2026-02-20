"""
Core API for reflective memory.

This is the minimal working implementation focused on:
- put(): fetch/embed → summarize → store
- find(): embed query → search
- get(): retrieve by ID
"""

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


def _parse_date_param(value: str) -> str:
    """
    Parse a date/duration parameter and return a YYYY-MM-DD date.

    Accepts:
    - ISO 8601 duration: P3D (3 days), P1W (1 week), PT1H (1 hour), P1DT12H, etc.
    - ISO date: 2026-01-15
    - Date with slashes: 2026/01/15

    Returns:
        YYYY-MM-DD string
    """
    since = value.strip()

    # ISO 8601 duration: P[n]Y[n]M[n]W[n]DT[n]H[n]M[n]S
    if since.upper().startswith("P"):
        duration_str = since.upper()

        # Parse duration components
        years = months = weeks = days = hours = minutes = seconds = 0

        # Split on T to separate date and time parts
        if "T" in duration_str:
            date_part, time_part = duration_str.split("T", 1)
        else:
            date_part = duration_str
            time_part = ""

        # Parse date part (P[n]Y[n]M[n]W[n]D)
        date_part = date_part[1:]  # Remove leading P
        for match in re.finditer(r"(\d+)([YMWD])", date_part):
            value, unit = int(match.group(1)), match.group(2)
            if unit == "Y":
                years = value
            elif unit == "M":
                months = value
            elif unit == "W":
                weeks = value
            elif unit == "D":
                days = value

        # Parse time part ([n]H[n]M[n]S)
        for match in re.finditer(r"(\d+)([HMS])", time_part):
            value, unit = int(match.group(1)), match.group(2)
            if unit == "H":
                hours = value
            elif unit == "M":
                minutes = value
            elif unit == "S":
                seconds = value

        # Convert to timedelta (approximate months/years)
        total_days = years * 365 + months * 30 + weeks * 7 + days
        delta = timedelta(days=total_days, hours=hours, minutes=minutes, seconds=seconds)
        cutoff = datetime.now(timezone.utc) - delta
        return cutoff.strftime("%Y-%m-%d")

    # Try parsing as date
    # ISO format: 2026-01-15 or 2026-01-15T...
    # Slash format: 2026/01/15
    date_str = since.replace("/", "-").split("T")[0]

    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        pass

    raise ValueError(
        f"Invalid date/duration format: {value}. "
        "Use ISO duration (P3D, PT1H, P1W) or date (2026-01-15)"
    )


def _filter_by_date(
    items: list,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list:
    """Filter items by date range (since <= date, date < until)."""
    since_cutoff = _parse_date_param(since) if since else None
    until_cutoff = _parse_date_param(until) if until else None
    result = []
    for item in items:
        d = item.tags.get("_updated_date", "0000-00-00")
        if since_cutoff and d < since_cutoff:
            continue
        if until_cutoff and d >= until_cutoff:
            continue
        result.append(item)
    return result


def _is_hidden(item) -> bool:
    """System notes (dot-prefix IDs like .conversations) are hidden by default."""
    base_id = item.tags.get("_base_id", item.id)
    return base_id.startswith(".")


def _truncate_ts(ts: str) -> str:
    """Normalize timestamp to canonical format: YYYY-MM-DDTHH:MM:SS.

    New data is already in this format (via utc_now()). This handles
    legacy timestamps that may have microseconds, 'Z', or '+00:00'.
    """
    # Strip fractional seconds
    dot = ts.find(".", 19)
    if dot != -1:
        # Skip past digits to any tz suffix
        end = dot
        for i in range(dot + 1, len(ts)):
            if ts[i] in "+-Z":
                break
        else:
            i = len(ts)
        ts = ts[:dot] + ts[i:] if i < len(ts) else ts[:dot]
    # Strip timezone suffix — all timestamps are UTC by convention
    if ts.endswith("+00:00"):
        ts = ts[:-6]
    elif ts.endswith("Z"):
        ts = ts[:-1]
    return ts


def _record_to_item(rec, score: float = None, changed: bool = None) -> "Item":
    """
    Convert a DocumentRecord to an Item with timestamp tags.

    Adds _updated, _created, _updated_date from the record's columns
    to ensure consistent timestamp exposure across all retrieval methods.
    """
    from .types import Item
    updated = _truncate_ts(rec.updated_at) if rec.updated_at else ""
    created = _truncate_ts(rec.created_at) if rec.created_at else ""
    accessed = _truncate_ts(rec.accessed_at or rec.updated_at) if (rec.accessed_at or rec.updated_at) else ""
    tags = {
        **rec.tags,
        "_updated": updated,
        "_created": created,
        "_updated_date": updated[:10],
        "_accessed": accessed,
        "_accessed_date": accessed[:10],
    }
    return Item(id=rec.id, summary=rec.summary, tags=tags, score=score, changed=changed)


import os
import subprocess
import sys

from .config import load_or_create_config, save_config, StoreConfig, EmbeddingIdentity
from .paths import get_config_dir, get_default_store_path
from .protocol import DocumentStoreProtocol, VectorStoreProtocol, PendingQueueProtocol
from .providers import get_registry
from .providers.base import (
    Document,
    DocumentProvider,
    EmbeddingProvider,
    MediaDescriber,
    SummarizationProvider,
)
from .providers.embedding_cache import CachingEmbeddingProvider
from .document_store import PartInfo, VersionInfo
from .types import (
    Item, ItemContext, SimilarRef, MetaRef, VersionRef, PartRef,
    casefold_tags, casefold_tags_for_index, filter_non_system_tags,
    SYSTEM_TAG_PREFIX, local_date,
    parse_utc_timestamp, validate_tag_key, validate_id, is_part_id,
    MAX_TAG_VALUE_LENGTH,
)


# Default max length for truncated placeholder summaries
TRUNCATE_LENGTH = 500

# Maximum attempts before giving up on a pending summary
MAX_SUMMARY_ATTEMPTS = 5


# Collection name validation: lowercase ASCII and underscores only

# Environment variable prefix for auto-applied tags
ENV_TAG_PREFIX = "KEEP_TAG_"

# Fixed ID for the current working context (singleton)
NOWDOC_ID = "now"


from .system_docs import (
    SYSTEM_DOC_DIR,
    SYSTEM_DOC_IDS,
    _load_frontmatter,
)

# Pattern for meta-doc query lines: key=value pairs separated by spaces
_META_QUERY_PAIR = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*=\S+$')
# Pattern for context-match lines: key= (bare, no value)
_META_CONTEXT_KEY = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)=$')
# Pattern for prerequisite lines: key=* (item must have this tag)
_META_PREREQ_KEY = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)=\*$')


def _parse_meta_doc(content: str) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """
    Parse meta-doc content into query lines, context-match keys, and prerequisites.

    Returns:
        (query_lines, context_keys, prereq_keys) where:
        - query_lines: list of dicts, each {key: value, ...} for AND queries
        - context_keys: list of tag keys for context matching
        - prereq_keys: list of tag keys the current item must have
    """
    query_lines: list[dict[str, str]] = []
    context_keys: list[str] = []
    prereq_keys: list[str] = []

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Check for prerequisite: exactly "key=*"
        prereq_match = _META_PREREQ_KEY.match(line)
        if prereq_match:
            prereq_keys.append(prereq_match.group(1))
            continue

        # Check for context-match: exactly "key=" with no value
        ctx_match = _META_CONTEXT_KEY.match(line)
        if ctx_match:
            context_keys.append(ctx_match.group(1))
            continue

        # Check for query line: all space-separated tokens are key=value
        tokens = line.split()
        pairs: dict[str, str] = {}
        is_query = True
        for token in tokens:
            if _META_QUERY_PAIR.match(token):
                k, v = token.split("=", 1)
                pairs[k] = v
            else:
                is_query = False
                break

        if is_query and pairs:
            query_lines.append(pairs)

    return query_lines, context_keys, prereq_keys

# Old IDs for migration (maps old → new)
def _get_env_tags() -> dict[str, str]:
    """
    Collect tags from KEEP_TAG_* environment variables.

    KEEP_TAG_PROJECT=foo -> {"project": "foo"}
    KEEP_TAG_MyTag=bar   -> {"mytag": "bar"}

    Tag keys are lowercased for consistency.
    """
    tags = {}
    for key, value in os.environ.items():
        if key.startswith(ENV_TAG_PREFIX) and value:
            tag_key = key[len(ENV_TAG_PREFIX):].lower()
            tags[tag_key] = value
    return tags


def _content_hash(content: str) -> str:
    """Short SHA256 hash of content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[-10:]


def _content_hash_full(content: str) -> str:
    """Full SHA256 hash of content for dedup verification."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _user_tags_changed(old_tags: dict, new_tags: dict) -> bool:
    """
    Check if non-system tags differ between old and new.

    Used for contextual re-summarization: when user tags change,
    the summary context changes and should be regenerated.

    Args:
        old_tags: Existing tags from document store
        new_tags: New merged tags being applied

    Returns:
        True if user (non-system) tags differ
    """
    old_user = {k: v for k, v in old_tags.items() if not k.startswith('_')}
    new_user = {k: v for k, v in new_tags.items() if not k.startswith('_')}
    return old_user != new_user


def _text_content_id(content: str) -> str:
    """
    Generate a content-addressed ID for text updates.

    This makes text updates versioned by content:
    - `keep put "my note"` → ID = %{hash[:12]}
    - `keep put "my note" -t status=done` → same ID, new version
    - `keep put "different note"` → different ID

    Args:
        content: The text content

    Returns:
        Content-addressed ID in format %{hash[:12]}
    """
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"%{content_hash}"


# -------------------------------------------------------------------------
# Decomposition helpers (module-level, used by Keeper.analyze)
# -------------------------------------------------------------------------

class Keeper:
    """
    Reflective memory keeper - persistent storage with similarity search.

    Example:
        kp = Keeper()
        kp.put(uri="file:///path/to/readme.md")
        results = kp.find("installation instructions")
    """
    
    def __init__(
        self,
        store_path: Optional[str | Path] = None,
        decay_half_life_days: float = 30.0,
        *,
        config: Optional[StoreConfig] = None,
        doc_store: Optional["DocumentStoreProtocol"] = None,
        vector_store: Optional["VectorStoreProtocol"] = None,
        pending_queue: Optional["PendingQueueProtocol"] = None,
    ) -> None:
        """
        Initialize or open an existing reflective memory store.

        Args:
            store_path: Path to store directory. Uses default if not specified.
                       Overrides any store.path setting in config.
            decay_half_life_days: Memory decay half-life in days (ACT-R model).
                After this many days, an item's effective relevance is halved.
                Set to 0 or negative to disable decay.
            config: Pre-loaded StoreConfig (skips filesystem config discovery).
            doc_store: Injected document store (skips default backend creation).
            vector_store: Injected vector store (skips default backend creation).
            pending_queue: Injected summary queue (skips default backend creation).
        """
        self._decay_half_life_days = decay_half_life_days

        # --- Config resolution ---
        if config is not None:
            # Injected config — skip filesystem discovery
            self._config: StoreConfig = config
            self._store_path = config.path if config.path else Path(".")
        else:
            # Resolve config and store paths from filesystem
            if store_path is not None:
                self._store_path = Path(store_path).resolve()
                config_dir = self._store_path
            else:
                config_dir = get_config_dir()

            self._config = load_or_create_config(config_dir)

            if store_path is None:
                self._store_path = get_default_store_path(self._config)

        # --- Document provider ---
        registry = get_registry()
        self._document_provider: DocumentProvider = registry.create_document(
            self._config.document.name,
            self._config.document.params,
        )
        self._apply_file_size_limit(self._document_provider)

        # Lazy-loaded providers (created on first use to avoid network access for read-only ops)
        self._embedding_provider: Optional[EmbeddingProvider] = None
        self._summarization_provider: Optional[SummarizationProvider] = None
        self._media_describer: Optional[MediaDescriber] = None
        self._analyzer = None  # AnalyzerProvider, lazy-loaded

        # Cache env tags once per Keeper instance (stable within a process)
        self._env_tags = casefold_tags(_get_env_tags())

        # --- Persistent operations log ---
        from .logging_config import configure_ops_log
        self._ops_log_handler = configure_ops_log(self._store_path)

        # --- Storage backends (injected or factory-created) ---
        if doc_store is not None and vector_store is not None:
            # Fully injected (tests, custom setups)
            from .backend import NullPendingQueue
            self._document_store = doc_store
            self._store = vector_store
            self._pending_queue = pending_queue or NullPendingQueue()
            self._is_local = False
        else:
            # Factory-based creation from config
            from .backend import create_stores
            bundle = create_stores(self._config)
            self._document_store = bundle.doc_store
            self._store = bundle.vector_store
            self._pending_queue = bundle.pending_queue
            self._is_local = bundle.is_local

        # Guard against concurrent background reconciliation
        import threading
        self._reconcile_lock = threading.Lock()
        self._reconcile_done = threading.Event()
        self._provider_init_lock = threading.Lock()

        # Check store consistency and reconcile in background if needed
        # (safe for all backends — uses abstract store interface)
        needs_reconcile = self._check_store_consistency() and self._config.embedding is not None

        # If cosine migration fired (L2→cosine), auto-enqueue reindex
        if getattr(self._store, "migrated_to_cosine", False):
            logger.info("Cosine migration detected — enqueuing reindex")
            try:
                stats = self.enqueue_reindex()
                import sys
                print(
                    f"Search index migrated to cosine similarity.\n"
                    f"Search is unavailable until reindex completes.\n"
                    f"Run: keep pending",
                    file=sys.stderr,
                )
            except Exception as e:
                logger.warning("Failed to enqueue reindex after cosine migration: %s", e)
            needs_reconcile = False  # reindex will handle everything

        if needs_reconcile:
            chroma_coll = self._resolve_chroma_collection()
            doc_coll = self._resolve_doc_collection()
            threading.Thread(
                target=self._auto_reconcile_safe, args=(chroma_coll, doc_coll), daemon=True
            ).start()
        else:
            self._reconcile_done.set()

        # System doc migration deferred to first write (needs embeddings)
        from .config import SYSTEM_DOCS_VERSION
        self._needs_sysdoc_migration = (
            self._config.system_docs_version < SYSTEM_DOCS_VERSION
        )

    def _apply_file_size_limit(self, provider: DocumentProvider) -> None:
        """Apply max_file_size config to file-based providers."""
        from .providers.documents import FileDocumentProvider, CompositeDocumentProvider
        max_size = self._config.max_file_size
        if isinstance(provider, FileDocumentProvider):
            provider.max_size = max_size
        elif isinstance(provider, CompositeDocumentProvider):
            for p in provider._providers:
                if isinstance(p, FileDocumentProvider):
                    p.max_size = max_size

    def _check_store_consistency(self) -> bool:
        """Check if document store and vector store ID sets match.

        Returns True if reconciliation is needed. Does not fix —
        that is deferred to the first _upsert call when the
        embedding provider is available.
        """
        try:
            chroma_coll = self._resolve_chroma_collection()
            doc_coll = self._resolve_doc_collection()
            doc_ids = self._document_store.list_ids(doc_coll)
            missing = self._store.find_missing_ids(chroma_coll, doc_ids)
            # Check for orphaned ChromaDB entries
            # Skip versioned (@v{N}) and part (@p{N}) IDs — they are stored
            # alongside main entries in ChromaDB but tracked in separate tables
            chroma_ids = self._store.list_ids(chroma_coll)
            doc_id_set = set(doc_ids)
            orphaned = [
                cid for cid in chroma_ids
                if cid not in doc_id_set and "@v" not in cid and "@p" not in cid
            ]
            if missing or orphaned:
                logger.info(
                    "Store inconsistency: %d missing from search index, %d orphaned (will auto-reconcile)",
                    len(missing), len(orphaned),
                )
                return True
        except Exception as e:
            logger.debug("Store consistency check failed: %s", e)
        return False

    def _auto_reconcile_safe(self, chroma_coll: str, doc_coll: str) -> None:
        """Background-safe wrapper for auto-reconcile. Logs failures."""
        if not self._reconcile_lock.acquire(blocking=False):
            logger.info("Reconciliation already in progress, skipping")
            return
        try:
            self._auto_reconcile(chroma_coll, doc_coll)
        except Exception as e:
            logger.warning("Auto-reconcile failed: %s", e)
        finally:
            self._reconcile_lock.release()
            self._reconcile_done.set()

    def _auto_reconcile(self, chroma_coll: str, doc_coll: str) -> None:
        """Fix store divergence using summaries (no content re-fetch needed).

        Requires an embedding provider to re-embed missing items.
        Skips entirely if no provider is configured or provider creation
        fails (avoids removing orphans without being able to re-embed).
        """
        if self._config.embedding is None:
            logger.info("Skipping reconciliation: no embedding provider configured")
            return

        # Validate provider before entering loop — catches broken installs
        # (e.g. mlx configured but not installed) early and cleanly
        try:
            provider = self._get_embedding_provider()
        except Exception as e:
            logger.warning("Skipping reconciliation: provider unavailable: %s", e)
            return

        doc_ids = self._document_store.list_ids(doc_coll)
        missing = self._store.find_missing_ids(chroma_coll, doc_ids)

        logger.info("Auto-reconcile started: %d missing, collection=%s", len(missing), chroma_coll)
        reconciled = 0
        failed = 0

        # Items in DocumentStore but missing from ChromaDB — re-embed summary
        for doc_id in missing:
            try:
                record = self._document_store.get(doc_coll, doc_id)
                if record:
                    embedding = provider.embed(record.summary)
                    # Re-verify document still exists after (potentially slow) embedding
                    if self._document_store.get(doc_coll, doc_id) is not None:
                        self._store.upsert(
                            collection=chroma_coll, id=doc_id,
                            embedding=embedding, summary=record.summary,
                            tags=casefold_tags_for_index(record.tags),
                        )
                        reconciled += 1
            except Exception as e:
                failed += 1
                logger.warning("Failed to reconcile %s: %s", doc_id, e)

        # Items in ChromaDB but not in DocumentStore — remove orphaned embeddings
        # Skip versioned (@v{N}) and part (@p{N}) IDs — tracked in separate tables
        chroma_ids = self._store.list_ids(chroma_coll)
        doc_id_set = set(doc_ids)
        removed = 0
        for orphan_id in chroma_ids:
            if orphan_id not in doc_id_set and "@v" not in orphan_id and "@p" not in orphan_id:
                try:
                    self._store.delete(chroma_coll, orphan_id)
                    removed += 1
                except Exception as e:
                    logger.warning("Failed to remove orphan %s: %s", orphan_id, e)

        logger.info(
            "Auto-reconcile complete: %d reconciled, %d failed, %d orphans removed",
            reconciled, failed, removed,
        )

    def _migrate_system_documents(self) -> dict:
        """Migrate system documents to stable IDs and current version."""
        from .system_docs import migrate_system_documents
        return migrate_system_documents(self)

    def _get_embedding_provider(self) -> EmbeddingProvider:
        """
        Get embedding provider, creating it lazily on first use.

        Thread-safe: uses a lock to prevent concurrent model loading
        when the reconcile thread and main thread both need embeddings.

        This allows read-only operations to work without loading
        the embedding model upfront.
        """
        if self._embedding_provider is not None:
            return self._embedding_provider

        with self._provider_init_lock:
            # Double-check after acquiring lock (another thread may have created it)
            if self._embedding_provider is not None:
                return self._embedding_provider

            if self._config.embedding is None:
                raise RuntimeError(
                    "No embedding provider configured.\n"
                    "\n"
                    "To use keep, configure a provider:\n"
                    "  API-based:  export VOYAGE_API_KEY=...  (or OPENAI_API_KEY, GEMINI_API_KEY)\n"
                    "  Local:      pip install 'keep-skill[local]'\n"
                    "\n"
                    "Read-only operations (get, list, find --text) work without embeddings."
                )
            registry = get_registry()
            base_provider = registry.create_embedding(
                self._config.embedding.name,
                self._config.embedding.params,
            )
            # Wrap local GPU providers with lifecycle lock
            # Local-only: model locks and embedding cache use filesystem
            if self._is_local:
                if self._config.embedding.name == "mlx":
                    from .model_lock import LockedEmbeddingProvider
                    base_provider = LockedEmbeddingProvider(
                        base_provider,
                        self._store_path / ".embedding.lock",
                    )
                cache_path = self._store_path / "embedding_cache.db"
                self._embedding_provider = CachingEmbeddingProvider(
                    base_provider,
                    cache_path=cache_path,
                )
            else:
                self._embedding_provider = base_provider
            # Validate or record embedding identity
            self._validate_embedding_identity(self._embedding_provider)
            # Update store's embedding dimension if it wasn't known at init
            if self._store.embedding_dimension is None:
                self._store.reset_embedding_dimension(self._embedding_provider.dimension)
        return self._embedding_provider

    def _try_dedup_embedding(
        self,
        doc_coll: str,
        chroma_coll: str,
        content_hash: Optional[str],
        exclude_id: str,
        content: str = "",
    ) -> Optional[list[float]]:
        """Look up an existing embedding from a donor doc with the same content hash.

        Returns the embedding if found and dimension-validated, None otherwise.
        Passes the full SHA256 for collision-safe verification.
        """
        if not content_hash:
            return None
        full_hash = _content_hash_full(content) if content else ""
        donor = self._document_store.find_by_content_hash(
            doc_coll, content_hash,
            content_hash_full=full_hash,
            exclude_id=exclude_id,
        )
        if donor is None:
            return None
        donor_embedding = self._store.get_embedding(chroma_coll, donor.id)
        if donor_embedding is None:
            return None
        if len(donor_embedding) != self._get_embedding_provider().dimension:
            return None
        logger.debug("Dedup: reusing embedding from %s for %s", donor.id, exclude_id)
        return donor_embedding

    def _get_summarization_provider(self) -> SummarizationProvider:
        """Get summarization provider, creating it lazily on first use."""
        if self._summarization_provider is None:
            registry = get_registry()
            provider = registry.create_summarization(
                self._config.summarization.name,
                self._config.summarization.params,
            )
            if self._is_local and self._config.summarization.name == "mlx":
                from .model_lock import LockedSummarizationProvider
                provider = LockedSummarizationProvider(
                    provider,
                    self._store_path / ".summarization.lock",
                )
            self._summarization_provider = provider
        return self._summarization_provider

    def _release_summarization_provider(self) -> None:
        """Release summarization model to free GPU/unified memory.

        Safe to call at any time — the lazy getter will reconstruct
        the provider on next use.
        """
        if self._summarization_provider is not None:
            if hasattr(self._summarization_provider, 'release'):
                self._summarization_provider.release()
            self._summarization_provider = None

    def _release_embedding_provider(self) -> None:
        """Release embedding model to free GPU/unified memory.

        Also closes the embedding cache. Safe to call at any time —
        the lazy getter will reconstruct both on next use.
        """
        if self._embedding_provider is not None:
            # Release the locked inner provider (frees model weights)
            inner = getattr(self._embedding_provider, '_provider', None)
            if hasattr(inner, 'release'):
                inner.release()
            # Close the embedding cache
            if hasattr(self._embedding_provider, '_cache'):
                cache = self._embedding_provider._cache
                if hasattr(cache, 'close'):
                    cache.close()
            self._embedding_provider = None

    def _get_media_describer(self) -> Optional[MediaDescriber]:
        """
        Get media describer, creating it lazily on first use.

        Returns None if no media provider is configured or creation fails.
        """
        if self._media_describer is None:
            if self._config.media is None:
                return None
            registry = get_registry()
            try:
                provider = registry.create_media(
                    self._config.media.name,
                    self._config.media.params,
                )
            except (ValueError, RuntimeError) as e:
                logger.warning("Media describer unavailable: %s", e)
                return None
            if self._is_local and self._config.media.name == "mlx":
                from .model_lock import LockedMediaDescriber
                provider = LockedMediaDescriber(
                    provider,
                    self._store_path / ".media.lock",
                )
            self._media_describer = provider
        return self._media_describer

    def _get_analyzer(self):
        """Get analyzer provider, creating it lazily on first use."""
        if self._analyzer is None:
            if self._config.analyzer:
                registry = get_registry()
                self._analyzer = registry.create_analyzer(
                    self._config.analyzer.name,
                    self._config.analyzer.params,
                )
            else:
                # Default: sliding-window analyzer with the summarization provider,
                # budget auto-selected based on the model's effective context quality.
                from .analyzers import SlidingWindowAnalyzer, get_budget_for_model
                provider = self._get_summarization_provider()
                model = getattr(provider, "model", "")
                provider_name = self._config.summarization.name if self._config.summarization else ""
                budget = get_budget_for_model(model, provider_name)
                self._analyzer = SlidingWindowAnalyzer(
                    provider=provider,
                    context_budget=budget,
                )
        return self._analyzer

    def _gather_context(
        self,
        id: str,
        tags: dict[str, str],
    ) -> str | None:
        """
        Gather related item summaries that share any user tag.

        Uses OR union (any tag matches), not AND intersection.
        Boosts score when multiple tags match.

        Args:
            id: ID of the item being summarized (to exclude from results)
            tags: User tags from the item being summarized

        Returns:
            Formatted context string, or None if no related items found
        """
        if not tags:
            return None

        # Get similar items (broader search, we'll filter by tags)
        try:
            similar = self.find(similar_to=id, limit=20)
        except KeyError:
            # Item not found yet (first indexing) - no context available
            return None

        # Score each item: similarity * (1 + matching_tag_count * boost)
        TAG_BOOST = 0.2  # 20% boost per matching tag
        scored: list[tuple[float, int, Item]] = []

        for item in similar:
            if item.id == id:
                continue

            # Count matching tags (OR: at least one must match)
            matching = sum(
                1 for k, v in tags.items()
                if item.tags.get(k) == v
            )
            if matching == 0:
                continue  # No tag overlap, skip

            # Boost score by number of matching tags
            base_score = item.score if item.score is not None else 0.5
            boosted_score = base_score * (1 + matching * TAG_BOOST)
            scored.append((boosted_score, matching, item))

        if not scored:
            return None

        # Sort by boosted score, take top 5
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:5]

        # Format context as topic keywords only (not summaries).
        # Including raw summary text causes small models to parrot
        # phrases from context into the new summary (contamination).
        topic_values = set()
        for _, _, item in top:
            for k, v in filter_non_system_tags(item.tags).items():
                topic_values.add(v)

        if not topic_values:
            return None

        return "Related topics: " + ", ".join(sorted(topic_values))

    def _validate_embedding_identity(self, provider: EmbeddingProvider) -> None:
        """
        Validate embedding provider matches stored identity, or record it.

        On first use, records the embedding identity to config.
        On subsequent uses, if the provider changed, silently updates config
        and enqueues reindex tasks into the pending queue.
        """
        # Get current provider's identity
        current = EmbeddingIdentity(
            provider=self._config.embedding.name,
            model=getattr(provider, "model_name", "unknown"),
            dimension=provider.dimension,
        )

        stored = self._config.embedding_identity

        if stored is None:
            # First use: record the identity
            logger.info(
                "Recording embedding identity: %s/%s (%dd)",
                current.provider, current.model, current.dimension,
            )
            self._config.embedding_identity = current
            save_config(self._config)
        else:
            # Check for provider change
            if (stored.provider != current.provider or
                stored.model != current.model or
                stored.dimension != current.dimension):
                logger.info(
                    "Embedding provider changed: %s/%s (%dd) → %s/%s (%dd)",
                    stored.provider, stored.model, stored.dimension,
                    current.provider, current.model, current.dimension,
                )
                self._config.embedding_identity = current
                save_config(self._config)
                # Update store dimension for new model
                self._store.reset_embedding_dimension(current.dimension)
                # Enqueue reindex tasks for pending queue
                stats = self.enqueue_reindex()
                logger.info(
                    "Enqueued %d items (+%d versions) for reindex",
                    stats["enqueued"], stats["versions"],
                )
                import sys
                print(
                    f"Embedding model changed. Enqueued {stats['enqueued']} items for reindex.\n"
                    f"Run: keep pending",
                    file=sys.stderr,
                )
            else:
                logger.debug(
                    "Embedding identity unchanged: %s/%s (%dd)",
                    stored.provider, stored.model, stored.dimension,
                )

    def enqueue_reindex(self) -> dict:
        """Enqueue embed tasks for all docs (and their versions) into the pending queue.

        Returns:
            Dict with stats: enqueued (int), versions (int)
        """
        doc_coll = self._resolve_doc_collection()
        doc_ids = self._document_store.list_ids(doc_coll)
        enqueued = 0
        versions = 0

        for doc_id in doc_ids:
            record = self._document_store.get(doc_coll, doc_id)
            if record is None:
                continue
            self._pending_queue.enqueue(
                doc_id, doc_coll, record.summary,
                task_type="reindex",
                metadata={"tags": dict(record.tags)},
            )
            enqueued += 1

            # Enqueue version reindex
            for vi in self._document_store.list_versions(doc_coll, doc_id, limit=100):
                version_id = f"{doc_id}@v{vi.version}"
                self._pending_queue.enqueue(
                    version_id, doc_coll, vi.summary,
                    task_type="reindex",
                    metadata={
                        "version": vi.version,
                        "base_id": doc_id,
                        "tags": dict(vi.tags),
                    },
                )
                versions += 1

        logger.info("Enqueue reindex: %d items + %d versions", enqueued, versions)
        return {"enqueued": enqueued, "versions": versions}

    # -------------------------------------------------------------------------
    # Data Export / Import
    # -------------------------------------------------------------------------

    def export_iter(self, *, include_system: bool = True) -> Iterator[dict]:
        """
        Stream-export all documents as an iterator of dicts.

        Yields dicts one at a time so that arbitrarily large stores can be
        exported without loading everything into memory.

        **First yield** — header dict::

            {"format": "keep-export", "version": 1, "exported_at": "...",
             "store_info": {"document_count": N, "version_count": N,
                            "part_count": N, "collection": "..."}}

        **Subsequent yields** — one dict per document. Each document is
        self-contained: its ``versions`` and ``parts`` lists are included
        inline (not yielded separately)::

            {"id": "...", "summary": "...", "tags": {...},
             "created_at": "...", "updated_at": "...", "accessed_at": "...",
             "versions": [...], "parts": [...]}

        Embeddings are excluded (model-dependent; regenerated on import).

        Args:
            include_system: If False, skip system documents (dot-prefix IDs)
        """
        doc_coll = self._resolve_doc_collection()
        doc_ids = self._document_store.list_ids(doc_coll)

        if not include_system:
            doc_ids = [d for d in doc_ids if not d.startswith(".")]

        # Header with document count; version/part counts filled per-document
        # as we stream (header store_info is best-effort for streaming)
        yield {
            "format": "keep-export",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "store_info": {
                "document_count": len(doc_ids),
                "version_count": sum(
                    self._document_store.version_count(doc_coll, d)
                    for d in doc_ids
                ),
                "part_count": sum(
                    self._document_store.part_count(doc_coll, d)
                    for d in doc_ids
                ),
                "collection": doc_coll,
            },
        }

        for doc_id in doc_ids:
            record = self._document_store.get(doc_coll, doc_id)
            if record is None:
                continue

            doc_dict: dict = {
                "id": record.id,
                "summary": record.summary,
                "tags": dict(record.tags),
                "content_hash": record.content_hash,
                "content_hash_full": record.content_hash_full,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "accessed_at": record.accessed_at,
            }

            # Versions — inline within the document dict
            versions = []
            for vi in self._document_store.list_versions(doc_coll, doc_id, limit=10000):
                versions.append({
                    "version": vi.version,
                    "summary": vi.summary,
                    "tags": dict(vi.tags),
                    "content_hash": vi.content_hash,
                    "created_at": vi.created_at,
                })
            if versions:
                doc_dict["versions"] = versions

            # Parts — inline within the document dict
            parts = []
            for pi in self._document_store.list_parts(doc_coll, doc_id):
                parts.append({
                    "part_num": pi.part_num,
                    "summary": pi.summary,
                    "tags": dict(pi.tags),
                    "content": pi.content,
                    "created_at": pi.created_at,
                })
            if parts:
                doc_dict["parts"] = parts

            yield doc_dict

    def export_data(self, *, include_system: bool = True) -> dict:
        """
        Export all documents, versions, and parts as a single dict.

        Convenience wrapper around :meth:`export_iter` that collects everything
        into memory.  Fine for small/medium stores; for large stores use
        ``export_iter()`` directly to stream documents.

        Returns:
            Dict in keep-export format (version 1) with a ``documents`` list.
        """
        it = self.export_iter(include_system=include_system)
        header = next(it)
        header["documents"] = list(it)
        return header

    def import_data(self, data: dict, *, mode: str = "merge") -> dict:
        """
        Import documents from an export dict.

        All SQLite writes happen in a single transaction for speed.
        Re-embedding is queued for background processing (not done inline).

        Args:
            data: Dict in keep-export format
            mode: "merge" (skip existing IDs) or "replace" (clear first)

        Returns:
            Dict with stats: {imported, skipped, versions, parts, queued}
        """
        if data.get("format") != "keep-export":
            raise ValueError("Invalid export format (expected 'keep-export')")
        if data.get("version", 0) > 1:
            raise ValueError(
                f"Export format version {data['version']} is not supported "
                f"(this version supports up to 1)"
            )

        doc_coll = self._resolve_doc_collection()
        documents = data.get("documents", [])

        if mode == "replace":
            self._document_store.delete_collection_all(doc_coll)
            chroma_coll = self._resolve_chroma_collection()
            try:
                self._store.delete_collection(chroma_coll)
            except Exception:
                pass  # ChromaDB collection may not exist yet

        # In merge mode, get existing IDs to skip
        existing_ids: set[str] = set()
        if mode == "merge":
            existing_ids = set(self._document_store.list_ids(doc_coll))

        # Filter to importable documents
        to_import = []
        skipped = 0
        for doc in documents:
            if doc["id"] in existing_ids:
                skipped += 1
            else:
                to_import.append(doc)

        # Batch-insert all documents, versions, parts in one transaction
        stats = self._document_store.import_batch(doc_coll, to_import)

        # Bulk-enqueue reindex tasks for all imported documents
        queued = 0
        for doc in to_import:
            doc_id = doc["id"]
            self._pending_queue.enqueue(
                doc_id, doc_coll, doc.get("summary", ""),
                task_type="reindex",
                metadata={"tags": doc.get("tags", {})},
            )
            queued += 1

            # Enqueue version reindex
            for ver in doc.get("versions", []):
                version_id = f"{doc_id}@v{ver['version']}"
                self._pending_queue.enqueue(
                    version_id, doc_coll, ver.get("summary", ""),
                    task_type="reindex",
                    metadata={
                        "version": ver["version"],
                        "base_id": doc_id,
                        "tags": ver.get("tags", {}),
                    },
                )

        return {
            "imported": stats["documents"],
            "skipped": skipped,
            "versions": stats["versions"],
            "parts": stats["parts"],
            "queued": queued,
        }

    @property
    def embedding_identity(self) -> EmbeddingIdentity | None:
        """Current embedding identity (provider, model, dimension)."""
        return self._config.embedding_identity
    
    def _resolve_chroma_collection(self) -> str:
        """Vector collection name derived from embedding identity."""
        if self._config.embedding_identity:
            return self._config.embedding_identity.key
        return "default"

    def _resolve_doc_collection(self) -> str:
        """DocumentStore collection — always 'default'."""
        return "default"
    
    # -------------------------------------------------------------------------
    # Constrained Tag Validation
    # -------------------------------------------------------------------------

    def _validate_constrained_tags(self, tags: dict[str, str]) -> None:
        """Check constrained tag values against sub-doc existence.

        For each user tag, looks up `.tag/KEY`. If that doc exists and has
        `_constrained=true`, checks that `.tag/KEY/VALUE` exists. Raises
        ValueError with valid values listed if not.
        """
        doc_coll = self._resolve_doc_collection()
        for key, value in tags.items():
            if key.startswith(SYSTEM_TAG_PREFIX):
                continue
            if value == "":
                continue  # deletion, no validation needed
            parent_id = f".tag/{key}"
            parent = self._document_store.get(doc_coll, parent_id)
            if parent is None:
                continue  # no tag doc → unconstrained
            if parent.tags.get("_constrained") != "true":
                continue  # tag doc exists but not constrained
            # Check sub-doc existence
            value_id = f".tag/{key}/{value}"
            if not self._document_store.get(doc_coll, value_id):
                valid = self._list_constrained_values(key)
                raise ValueError(
                    f"Invalid value for constrained tag '{key}': {value!r}. "
                    f"Valid values: {', '.join(sorted(valid))}"
                )

    def _list_constrained_values(self, key: str) -> list[str]:
        """List valid values for a constrained tag by finding sub-docs."""
        doc_coll = self._resolve_doc_collection()
        prefix = f".tag/{key}/"
        docs = self._document_store.query_by_id_prefix(doc_coll, prefix)
        return [doc.id[len(prefix):] for doc in docs]

    # -------------------------------------------------------------------------
    # Write Operations
    # -------------------------------------------------------------------------

    def _upsert(
        self,
        id: str,
        content: str,
        *,
        tags: Optional[dict[str, str]] = None,
        summary: Optional[str] = None,
        system_tags: dict[str, str],
        created_at: Optional[str] = None,
    ) -> Item:
        """Core upsert logic used by put()."""
        # Wait for background reconciliation to finish before writing.
        # The reconcile thread and main thread both access the embedding
        # provider, ChromaDB, and SQLite — concurrent access causes hangs
        # with ChromaDB's Rust bindings and double model loading.
        self._reconcile_done.wait(timeout=120)

        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Deferred init tasks (run on first write when embeddings are available)
        if self._needs_sysdoc_migration:
            self._needs_sysdoc_migration = False  # Clear before call (migration calls remember → _upsert)
            self._migrate_system_documents()

        # Get existing item to preserve tags (check document store first, fall back to ChromaDB)
        existing_tags = {}
        existing_doc = self._document_store.get(doc_coll, id)
        if existing_doc:
            existing_tags = filter_non_system_tags(existing_doc.tags)
        else:
            existing = self._store.get(chroma_coll, id)
            if existing:
                existing_tags = filter_non_system_tags(existing.tags)

        # Compute content hash for change detection
        new_hash = _content_hash(content)

        # Build tags: existing → config → env → user → system
        merged_tags = {**existing_tags}

        if self._config.default_tags:
            merged_tags.update(casefold_tags(self._config.default_tags))

        merged_tags.update(self._env_tags)

        if tags:
            user_tags = casefold_tags(filter_non_system_tags(tags))
            merged_tags.update(user_tags)
            # Validate constrained tags (only user-provided, not existing/env)
            self._validate_constrained_tags(user_tags)

        merged_tags.update(system_tags)

        # Change detection (before embedding to allow early return)
        content_unchanged = (
            existing_doc is not None
            and existing_doc.content_hash == new_hash
        )
        tags_changed = (
            existing_doc is not None
            and _user_tags_changed(existing_doc.tags, merged_tags)
        )

        # Early return: nothing to do
        if content_unchanged and not tags_changed and summary is None:
            logger.debug("Content and tags unchanged, skipping for %s", id)
            return _record_to_item(existing_doc, changed=False)

        # Determine summary
        max_len = self._config.max_summary_length
        if summary is not None:
            if len(summary) > max_len:
                import warnings
                warnings.warn(
                    f"Summary exceeds max_summary_length ({len(summary)} > {max_len}), truncating",
                    UserWarning,
                    stacklevel=3
                )
                summary = summary[:max_len]
            final_summary = summary
        elif content_unchanged and tags_changed:
            logger.debug("Tags changed, queueing re-summarization for %s", id)
            final_summary = existing_doc.summary
            if len(content) > max_len:
                self._pending_queue.enqueue(id, doc_coll, content)
        elif len(content) <= max_len:
            final_summary = content
        else:
            final_summary = content[:max_len] + "..."
            self._pending_queue.enqueue(id, doc_coll, content)

        # Cloud mode: defer embedding to background worker for faster response.
        # The doc store write happens immediately; the note is findable by
        # tags/fulltext/ID right away. Similarity search works once the
        # background worker computes and stores the embedding.
        if not self._is_local:
            result, content_changed = self._document_store.upsert(
                collection=doc_coll,
                id=id,
                summary=final_summary,
                tags=merged_tags,
                content_hash=new_hash,
                content_hash_full=_content_hash_full(content),
                created_at=created_at,
            )
            # Try embedding dedup before enqueueing (saves network round-trip)
            if not content_unchanged:
                donor_embedding = self._try_dedup_embedding(
                    doc_coll, chroma_coll, new_hash, id, content,
                )
                if donor_embedding is not None:
                    self._store.upsert(
                        collection=chroma_coll, id=id,
                        embedding=donor_embedding,
                        summary=final_summary,
                        tags=casefold_tags_for_index(merged_tags),
                    )
                    return _record_to_item(result, changed=True)
            # Enqueue embedding task (content needed for embedding computation)
            embed_meta = {}
            if existing_doc is not None and not content_unchanged:
                embed_meta["content_changed"] = True
            self._pending_queue.enqueue(
                id, doc_coll, content,
                task_type="embed",
                metadata=embed_meta,
            )
            return _record_to_item(result, changed=not content_unchanged)

        # Local mode: compute embedding synchronously
        if content_unchanged:
            embedding = self._store.get_embedding(chroma_coll, id)
            if embedding is None:
                embedding = self._try_dedup_embedding(
                    doc_coll, chroma_coll, new_hash, id, content,
                )
            if embedding is None:
                embedding = self._get_embedding_provider().embed(content)
        else:
            embedding = self._try_dedup_embedding(doc_coll, chroma_coll, new_hash, id, content)
            if embedding is None:
                embedding = self._get_embedding_provider().embed(content)

        # Save old embedding before ChromaDB upsert overwrites it (for version archival)
        old_embedding = None
        if existing_doc is not None and not content_unchanged:
            old_embedding = self._store.get_embedding(chroma_coll, id)

        # Dual-write: document store (canonical) + ChromaDB (embedding index)
        result, content_changed = self._document_store.upsert(
            collection=doc_coll,
            id=id,
            summary=final_summary,
            tags=merged_tags,
            content_hash=new_hash,
            content_hash_full=_content_hash_full(content),
            created_at=created_at,
        )

        self._store.upsert(
            collection=chroma_coll,
            id=id,
            embedding=embedding,
            summary=final_summary,
            tags=casefold_tags_for_index(merged_tags),
        )

        # If content changed and we archived a version, also store versioned embedding
        if existing_doc is not None and content_changed:
            max_ver = self._document_store.max_version(doc_coll, id)
            if max_ver > 0:
                if old_embedding is None:
                    old_embedding = self._get_embedding_provider().embed(existing_doc.summary)
                self._store.upsert_version(
                    collection=chroma_coll,
                    id=id,
                    version=max_ver,
                    embedding=old_embedding,
                    summary=existing_doc.summary,
                    tags=casefold_tags_for_index(existing_doc.tags),
                )

        # Spawn background processor if needed (local only — uses filesystem locks)
        if summary is None and len(content) > max_len and (not content_unchanged or tags_changed):
            self._spawn_processor()

        return _record_to_item(result, changed=not content_unchanged)

    def put(
        self,
        content: Optional[str] = None,
        *,
        uri: Optional[str] = None,
        id: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[dict[str, str]] = None,
        created_at: Optional[str] = None,
    ) -> Item:
        """
        Store content in the memory.

        Provide either inline content or a URI to fetch — not both.

        **Inline mode** (content provided):
        - Stores text directly. Auto-generates an ID if not provided.
        - Short content is used verbatim as summary. Large content gets
          async summarization (truncated placeholder stored immediately).

        **URI mode** (uri provided):
        - Fetches the document, extracts text, generates embeddings.
        - Supports file://, http://, https:// URIs.
        - Non-text content (images, audio, PDF) gets media description.

        **Tag and summary behavior:**
        - Tags are merged with existing tags (new override on collision).
        - System tags (_prefixed) are managed automatically.
        - If summary is provided, it's used directly (skips auto-summarization).

        Args:
            content: Inline text to store
            uri: URI of document to fetch and index
            id: Custom ID (auto-generated for inline content if None)
            summary: User-provided summary (skips auto-summarization)
            tags: User-provided tags to merge with existing tags
            created_at: Override creation timestamp (ISO 8601).
                        For importing historical data via the Python API.
                        When updating an existing item, sets the head's
                        created_at so version archives carry accurate dates.

        Returns:
            The stored Item with merged tags and summary
        """
        if content is not None and uri is not None:
            raise ValueError("Provide content or uri, not both")
        if content is None and uri is None:
            raise ValueError("Either content or uri is required")

        # Validate and normalize tags (shared by both URI and inline paths)
        if tags:
            tags = casefold_tags(tags)
            for key, value in tags.items():
                if not key.startswith(SYSTEM_TAG_PREFIX):
                    validate_tag_key(key)
                    if len(value) > MAX_TAG_VALUE_LENGTH:
                        raise ValueError(f"Tag value too long (max {MAX_TAG_VALUE_LENGTH}): {key!r}")
            self._validate_constrained_tags(
                {k: v for k, v in tags.items()
                 if not k.startswith(SYSTEM_TAG_PREFIX) and v != ""}
            )

        # Parts are immutable — block put() with part-like IDs
        effective_id = id or uri or ""
        if is_part_id(effective_id):
            raise ValueError(
                f"Cannot modify part directly: {effective_id!r}. "
                "Parts are managed by analyze()."
            )

        # Enforce required tags (skip for system docs with dot-prefix IDs)
        if self._config.required_tags and not effective_id.startswith("."):
            user_tags = {k: v for k, v in (tags or {}).items()
                         if not k.startswith(SYSTEM_TAG_PREFIX)} if tags else {}
            missing = [t for t in self._config.required_tags if t not in user_tags]
            if missing:
                raise ValueError(f"Required tags missing: {', '.join(missing)}")

        if uri is not None:
            # URI mode: fetch document, extract content, store
            validate_id(uri)

            # Fast path for local files: skip expensive read if stat unchanged
            is_file_uri = uri.startswith("file://") or uri.startswith("/")
            if is_file_uri and summary is None:
                try:
                    fpath = Path(uri.removeprefix("file://")).resolve()
                    st = fpath.stat()
                    doc_coll = self._resolve_doc_collection()
                    existing = self._document_store.get(doc_coll, uri)
                    if (existing
                            and existing.tags.get("_file_mtime_ns") == str(st.st_mtime_ns)
                            and existing.tags.get("_file_size") == str(st.st_size)):
                        # File stat unchanged — check if tags would also be unchanged
                        if not tags or not _user_tags_changed(
                                existing.tags,
                                {**filter_non_system_tags(existing.tags),
                                 **casefold_tags(tags)}):
                            logger.debug("File stat unchanged, skipping read for %s", uri)
                            return _record_to_item(existing, changed=False)
                except OSError:
                    pass  # Fall through to normal fetch

            doc = self._document_provider.fetch(uri)

            # Merge provider-extracted tags with user tags (user wins on collision)
            merged_tags: dict[str, str] | None = None
            if doc.tags or tags:
                merged_tags = {}
                if doc.tags:
                    merged_tags.update(doc.tags)
                if tags:
                    merged_tags.update(tags)

            # Media description: enrich non-text content
            if doc.content_type and not doc.content_type.startswith("text/"):
                describer = self._get_media_describer()
                if describer:
                    try:
                        file_path = uri.removeprefix("file://") if uri.startswith("file://") else uri
                        description = describer.describe(file_path, doc.content_type)
                        if description:
                            doc = Document(
                                uri=doc.uri,
                                content=doc.content + "\n\nDescription:\n" + description,
                                content_type=doc.content_type,
                                metadata=doc.metadata,
                                tags=doc.tags,
                            )
                            logger.info("Added media description for %s (%d chars)",
                                        uri, len(description))
                    except Exception as e:
                        logger.warning("Media description failed for %s: %s", uri, e)

            system_tags = {"_source": "uri"}
            if doc.content_type:
                system_tags["_content_type"] = doc.content_type

            # Store file stat for fast-path change detection on next put
            if is_file_uri and doc.metadata:
                try:
                    fpath = Path(uri.removeprefix("file://")).resolve()
                    st = fpath.stat()
                    system_tags["_file_mtime_ns"] = str(st.st_mtime_ns)
                    system_tags["_file_size"] = str(st.st_size)
                except OSError:
                    pass

            # Use file birthtime as created_at for new items
            if created_at is None and is_file_uri and doc.metadata:
                birthtime = doc.metadata.get("birthtime")
                if birthtime is not None:
                    created_at = datetime.fromtimestamp(
                        birthtime, tz=timezone.utc
                    ).isoformat()

            return self._upsert(
                uri, doc.content,
                tags=merged_tags, summary=summary,
                system_tags=system_tags,
                created_at=created_at,
            )
        else:
            # Inline mode: store content directly
            if id is None:
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
                id = f"mem:{timestamp}"
            else:
                validate_id(id)

            return self._upsert(
                id, content,
                tags=tags, summary=summary,
                system_tags={"_source": "inline"},
                created_at=created_at,
            )

    # -------------------------------------------------------------------------
    # Query Operations
    # -------------------------------------------------------------------------
    
    def _apply_recency_decay(self, items: list[Item]) -> list[Item]:
        """
        Apply ACT-R style recency decay to search results.
        
        Multiplies each item's similarity score by a decay factor based on
        time since last update. Uses exponential decay with configurable half-life.
        
        Formula: effective_score = similarity × 0.5^(days_elapsed / half_life)
        """
        if self._decay_half_life_days <= 0:
            return items  # Decay disabled
        
        now = datetime.now(timezone.utc)
        decayed_items = []
        
        for item in items:
            # Get last update time from tags
            updated_str = item.tags.get("_updated")
            if updated_str and item.score is not None:
                try:
                    updated = parse_utc_timestamp(updated_str)
                    days_elapsed = (now - updated).total_seconds() / 86400
                    
                    # Exponential decay: 0.5^(days/half_life)
                    decay_factor = 0.5 ** (days_elapsed / self._decay_half_life_days)
                    decayed_score = item.score * decay_factor
                    
                    # Create new Item with decayed score
                    decayed_items.append(Item(
                        id=item.id,
                        summary=item.summary,
                        tags=item.tags,
                        score=decayed_score
                    ))
                except (ValueError, TypeError):
                    # If timestamp parsing fails, keep original
                    decayed_items.append(item)
            else:
                decayed_items.append(item)
        
        # Re-sort by decayed score (highest first)
        decayed_items.sort(key=lambda x: x.score if x.score is not None else 0, reverse=True)
        
        return decayed_items
    
    def find(
        self,
        query: Optional[str] = None,
        *,
        tags: Optional[dict[str, str]] = None,
        similar_to: Optional[str] = None,
        fulltext: bool = False,
        limit: int = 10,
        since: Optional[str] = None,
        until: Optional[str] = None,
        include_self: bool = False,
        include_hidden: bool = False,
    ) -> list[Item]:
        """
        Find items by semantic similarity, full-text search, or similarity to an existing note.

        Exactly one of `query` or `similar_to` must be provided.

        Args:
            query: Search query text (semantic by default, fulltext if fulltext=True)
            tags: Optional tag filter — only return items matching all specified tags
            similar_to: Find items similar to this note ID
            fulltext: Use full-text search instead of semantic similarity (only with query)
            limit: Maximum results to return
            since: Only include items updated since (ISO duration like P3D, or date)
            until: Only include items updated before (ISO duration like P3D, or date)
            include_self: Include the queried item in results (only with similar_to)
            include_hidden: Include system notes (dot-prefix IDs)
        """
        if query and similar_to:
            raise ValueError("Specify either query or similar_to, not both")
        if not query and not similar_to:
            raise ValueError("Specify either query or similar_to")
        if fulltext and similar_to:
            raise ValueError("fulltext cannot be used with similar_to")

        chroma_coll = self._resolve_chroma_collection()
        doc_coll = self._resolve_doc_collection()

        # Build where clause from tags filter
        where = None
        if tags:
            casefolded = {k.casefold(): v.casefold() for k, v in tags.items()}
            conditions = [{k: v} for k, v in casefolded.items()]
            where = conditions[0] if len(conditions) == 1 else {"$and": conditions}

        if similar_to:
            # Similar-to mode: use stored embedding from existing item
            item = self._store.get(chroma_coll, similar_to)
            if item is None:
                raise KeyError(f"Item not found: {similar_to}")

            embedding = self._store.get_embedding(chroma_coll, similar_to)
            if embedding is None:
                embedding = self._get_embedding_provider().embed(item.summary)
            actual_limit = (limit + 1 if not include_self else limit) * 3
            results = self._store.query_embedding(chroma_coll, embedding, limit=actual_limit, where=where)

            if not include_self:
                results = [r for r in results if r.id != similar_to]

            items = [r.to_item() for r in results]
            items = self._apply_recency_decay(items)

        elif fulltext:
            # Full-text mode: text matching
            fetch_limit = limit * 3
            results = self._store.query_fulltext(chroma_coll, query, limit=fetch_limit, where=where)
            items = [r.to_item() for r in results]

        else:
            # Semantic mode (default): embed query, search by similarity
            embedding = self._get_embedding_provider().embed(query)
            fetch_limit = limit * 3 if self._decay_half_life_days > 0 else limit * 2
            results = self._store.query_embedding(chroma_coll, embedding, limit=fetch_limit, where=where)

            items = [r.to_item() for r in results]
            items = self._apply_recency_decay(items)

        # Apply common filters
        if since is not None or until is not None:
            items = _filter_by_date(items, since=since, until=until)
        if not include_hidden:
            items = [i for i in items if not _is_hidden(i)]

        # Part-to-parent uplift: replace part hits with their parent
        # documents, carrying _focus_part so the formatter can window
        # the parts manifest around the hit.  Dedup: keep the highest-
        # scoring part when multiple parts of the same parent match.
        uplifted: list[Item] = []
        seen_parents: dict[str, int] = {}  # parent_id -> index in uplifted
        for item in items:
            if is_part_id(item.id):
                parent_id = item.tags.get("_base_id", item.id.split("@")[0])
                part_num = item.tags.get("_part_num")
                if parent_id in seen_parents:
                    # Already have this parent — skip (first hit had higher score)
                    continue
                parent_doc = self._document_store.get(doc_coll, parent_id)
                if parent_doc:
                    parent_item = _record_to_item(parent_doc)
                    parent_tags = dict(parent_item.tags)
                    if part_num:
                        parent_tags["_focus_part"] = part_num
                    uplifted.append(Item(
                        id=parent_id, summary=parent_item.summary,
                        tags=parent_tags, score=item.score,
                    ))
                    seen_parents[parent_id] = len(uplifted) - 1
                else:
                    uplifted.append(item)  # Parent gone — keep raw part
            else:
                # Regular document — dedup against uplifted parents
                if item.id in seen_parents:
                    continue
                uplifted.append(item)
                seen_parents[item.id] = len(uplifted) - 1
        items = uplifted

        final = items[:limit]
        # Enrich tags from SQLite (ChromaDB stores casefolded values;
        # SQLite has the canonical original-case values for display)
        if final:
            self._document_store.touch_many(doc_coll, [i.id for i in final])
            enriched = []
            for item in final:
                doc = self._document_store.get(doc_coll, item.id)
                if doc:
                    tags = dict(doc.tags)
                    # Preserve _focus_part from uplift
                    focus = item.tags.get("_focus_part")
                    if focus:
                        tags["_focus_part"] = focus
                    enriched.append(Item(
                        id=item.id, summary=item.summary,
                        tags=tags, score=item.score,
                    ))
                else:
                    enriched.append(item)
            final = enriched
        return final

    def get_context(
        self,
        id: str,
        *,
        version: int | None = None,
        similar_limit: int = 3,
        meta_limit: int = 3,
        include_similar: bool = True,
        include_meta: bool = True,
        include_parts: bool = True,
        include_versions: bool = True,
    ) -> ItemContext | None:
        """Assemble complete display context for a single item.

        Single implementation of the 5-call assembly pattern used by
        CLI get, now, and put commands.  Returns None if the item
        doesn't exist.

        Args:
            id: Document identifier
            version: Version offset (0 or None = current, 1 = previous, ...)
            similar_limit: Max similar items to include
            meta_limit: Max items per meta-doc section
            include_similar: Whether to resolve similar items
            include_meta: Whether to resolve meta-doc sections
            include_parts: Whether to include parts manifest
            include_versions: Whether to include version navigation
        """
        offset = version or 0
        if offset > 0:
            item = self.get_version(id, offset)
        else:
            item = self.get(id)
        if item is None:
            return None

        # Version navigation
        prev_refs: list[VersionRef] = []
        next_refs: list[VersionRef] = []
        if include_versions:
            nav = self.get_version_nav(id, version)
            for i, v in enumerate(nav.get("prev", [])):
                prev_refs.append(VersionRef(
                    offset=offset + i + 1,
                    date=local_date(v.tags.get("_created") or v.created_at or ""),
                    summary=v.summary,
                ))
            for i, v in enumerate(nav.get("next", [])):
                next_refs.append(VersionRef(
                    offset=offset - i - 1,
                    date=local_date(v.tags.get("_created") or v.created_at or ""),
                    summary=v.summary,
                ))

        # Similar items (only for current version)
        similar_refs: list[SimilarRef] = []
        if include_similar and offset == 0:
            raw = self.get_similar_for_display(id, limit=similar_limit)
            for s in raw:
                s_offset = self.get_version_offset(s)
                similar_refs.append(SimilarRef(
                    id=s.tags.get("_base_id", s.id),
                    offset=s_offset,
                    score=s.score,
                    date=local_date(
                        s.tags.get("_updated") or s.tags.get("_created", "")
                    ),
                    summary=s.summary,
                ))

        # Meta-doc sections (only for current version)
        meta_refs: dict[str, list[MetaRef]] = {}
        if include_meta and offset == 0:
            raw_meta = self.resolve_meta(id, limit_per_doc=meta_limit)
            for name, meta_items in raw_meta.items():
                meta_refs[name] = [
                    MetaRef(id=mi.id, summary=mi.summary)
                    for mi in meta_items
                ]

        # Parts manifest (only for current version)
        part_refs: list[PartRef] = []
        if include_parts and offset == 0:
            for p in self.list_parts(id):
                part_refs.append(PartRef(
                    part_num=p.part_num,
                    summary=p.summary,
                    tags=dict(p.tags),
                ))

        return ItemContext(
            item=item,
            viewing_offset=offset,
            similar=similar_refs,
            meta=meta_refs,
            parts=part_refs,
            prev=prev_refs,
            next=next_refs,
        )

    def get_similar_for_display(
        self,
        id: str,
        *,
        limit: int = 3,
    ) -> list[Item]:
        """
        Find similar items for frontmatter display using stored embedding.

        Optimized for display: uses stored embedding (no re-embedding),
        filters to distinct base documents, excludes source document versions.

        Args:
            id: ID of item to find similar items for
            limit: Maximum results to return

        Returns:
            List of similar items, one per unique base document
        """
        chroma_coll = self._resolve_chroma_collection()

        # Get the stored embedding (no re-embedding)
        embedding = self._store.get_embedding(chroma_coll, id)
        if embedding is None:
            return []

        # Fetch more than needed to account for version/hidden filtering
        fetch_limit = limit * 5
        results = self._store.query_embedding(chroma_coll, embedding, limit=fetch_limit)

        # Convert to Items
        items = [r.to_item() for r in results]

        # Extract base ID of source document
        source_base_id = id.split("@v")[0] if "@v" in id else id

        # Filter to distinct base IDs, excluding source document and hidden notes
        seen_base_ids: set[str] = set()
        filtered: list[Item] = []
        for item in items:
            # Get base ID from tags or parse from ID
            base_id = item.tags.get("_base_id", item.id.split("@v")[0] if "@v" in item.id else item.id)

            # Skip versions of source document and hidden system notes
            if base_id == source_base_id or base_id.startswith("."):
                continue

            # Keep only first version of each document
            if base_id not in seen_base_ids:
                seen_base_ids.add(base_id)
                filtered.append(item)

                if len(filtered) >= limit:
                    break

        return filtered

    def get_version_offset(self, item: Item) -> int:
        """
        Get version offset (0=current, 1=previous, ...) for an item.

        Converts the internal version number (1=oldest, 2=next...) to the
        user-visible offset format (0=current, 1=previous, 2=two-ago...).

        Args:
            item: Item to get version offset for

        Returns:
            Version offset (0 for current version)
        """
        version_tag = item.tags.get("_version")
        if not version_tag:
            return 0  # Current version
        base_id = item.tags.get("_base_id", item.id)
        doc_coll = self._resolve_doc_collection()
        # Count versions >= this one to get the offset (handles gaps)
        internal_version = int(version_tag)
        return self._document_store.count_versions_from(
            doc_coll, base_id, internal_version
        )

    def resolve_meta(
        self,
        item_id: str,
        *,
        limit_per_doc: int = 3,
    ) -> dict[str, list[Item]]:
        """
        Resolve all .meta/* docs against an item's tags.

        Meta-docs define tag-based queries that surface contextually relevant
        items — open commitments, past learnings, decisions to revisit.
        Results are ranked by similarity to the current item + recency decay,
        so the most relevant matches surface first.

        Args:
            item_id: ID of the item whose tags provide context
            limit_per_doc: Max results per meta-doc

        Returns:
            Dict of {meta_name: [matching Items]}. Empty results omitted.
        """
        doc_coll = self._resolve_doc_collection()

        # Find all .meta/* documents
        meta_records = self._document_store.query_by_id_prefix(doc_coll, ".meta/")
        if not meta_records:
            return {}

        # Get current item's tags for context
        current = self.get(item_id)
        if current is None:
            return {}
        current_tags = current.tags

        result: dict[str, list[Item]] = {}

        for rec in meta_records:
            meta_id = rec.id
            short_name = meta_id.split("/", 1)[1] if "/" in meta_id else meta_id

            query_lines, context_keys, prereq_keys = _parse_meta_doc(rec.summary)
            if not query_lines and not context_keys:
                continue

            matches = self._resolve_meta_queries(
                item_id, current_tags, query_lines, context_keys, prereq_keys, limit_per_doc,
            )
            if matches:
                result[short_name] = matches

        return result

    def resolve_inline_meta(
        self,
        item_id: str,
        queries: list[dict[str, str]],
        context_keys: list[str] | None = None,
        prereq_keys: list[str] | None = None,
        *,
        limit: int = 3,
    ) -> list[Item]:
        """
        Resolve an inline meta query against an item's tags.

        Like resolve_meta() but with ad-hoc queries instead of persistent
        .meta/* documents. Queries use the same tag-based syntax.

        Args:
            item_id: ID of the item whose tags provide context
            queries: List of tag-match dicts, each {key: value} for AND queries;
                     multiple dicts are OR (union)
            context_keys: Tag keys to expand from the current item's tags
            prereq_keys: Tag keys the current item must have (or return empty)
            limit: Max results

        Returns:
            List of matching Items, ranked by similarity + recency.
        """
        current = self.get(item_id)
        if current is None:
            return []

        return self._resolve_meta_queries(
            item_id, current.tags,
            queries, context_keys or [], prereq_keys or [], limit,
        )

    def _resolve_meta_queries(
        self,
        item_id: str,
        current_tags: dict[str, str],
        query_lines: list[dict[str, str]],
        context_keys: list[str],
        prereq_keys: list[str],
        limit: int,
    ) -> list[Item]:
        """Shared resolution logic for persistent and inline metadocs."""
        # Check prerequisites: current item must have all required tags
        if prereq_keys:
            if not all(current_tags.get(k) for k in prereq_keys):
                return []

        # Get context values from current item's tags
        context_values: dict[str, str] = {}
        for key in context_keys:
            val = current_tags.get(key)
            if val and not key.startswith("_"):
                context_values[key] = val

        # Build expanded queries: cross-product of query lines × context values
        expanded: list[dict[str, str]] = []
        if context_values and query_lines:
            for query in query_lines:
                for ctx_key, ctx_val in context_values.items():
                    expanded.append({**query, ctx_key: ctx_val})
        elif context_values:
            # Context-only (group-by): each context value becomes a query
            for ctx_key, ctx_val in context_values.items():
                expanded.append({ctx_key: ctx_val})
        else:
            # No context → use query lines as-is
            expanded = list(query_lines)

        # Run each expanded query, union results (fetch generously for ranking)
        seen_ids: set[str] = set()
        matches: list[Item] = []
        for query in expanded:
            try:
                items = self.list_items(
                    tags=query,
                    limit=100,  # fetch all candidates for ranking
                )
            except (ValueError, Exception):
                continue
            for item in items:
                # Skip the current item, hidden system notes, and dupes
                if item.id == item_id or _is_hidden(item) or item.id in seen_ids:
                    continue
                seen_ids.add(item.id)
                matches.append(item)

        if not matches:
            return []

        # Rank by similarity to current item + recency decay
        matches = self._rank_by_relevance(self._resolve_chroma_collection(), item_id, matches)
        return matches[:limit]

    def _rank_by_relevance(
        self,
        coll: str,
        anchor_id: str,
        candidates: list[Item],
    ) -> list[Item]:
        """
        Rank candidate items by similarity to anchor + recency decay.

        Uses stored embeddings — no re-embedding needed.
        Falls back to recency-only ranking if embeddings unavailable.
        """
        import math

        if not candidates:
            return candidates

        # Get anchor + candidate embeddings from store
        try:
            candidate_ids = [c.id for c in candidates]
            all_ids = [anchor_id] + candidate_ids
            entries = self._store.get_entries_full(coll, all_ids)
        except Exception as e:
            logger.debug("Embedding lookup failed, falling back to recency: %s", e)
            return self._apply_recency_decay(candidates)

        # Build id → embedding lookup
        emb_lookup: dict[str, list[float]] = {}
        for entry in entries:
            if entry.get("embedding") is not None:
                emb_lookup[entry["id"]] = entry["embedding"]

        # Extract anchor embedding
        anchor_emb = emb_lookup.get(anchor_id)
        if anchor_emb is None:
            return self._apply_recency_decay(candidates)

        # Score each candidate: cosine similarity
        def _cosine_sim(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)

        scored = []
        for item in candidates:
            emb = emb_lookup.get(item.id)
            sim = _cosine_sim(anchor_emb, emb) if emb is not None else 0.0
            scored.append(Item(id=item.id, summary=item.summary, tags=item.tags, score=sim))

        # Apply recency decay to the similarity scores
        candidates = self._apply_recency_decay(scored)

        # Sort by score descending
        candidates.sort(key=lambda x: x.score or 0.0, reverse=True)
        return candidates

    def list_tags(
        self,
        key: Optional[str] = None,
    ) -> list[str]:
        """
        List distinct tag keys or values.

        Args:
            key: If provided, list distinct values for this key.
                 If None, list distinct tag keys.

        Returns:
            Sorted list of distinct keys or values
        """
        if key is not None:
            validate_tag_key(key)
        doc_coll = self._resolve_doc_collection()

        if key is None:
            return self._document_store.list_distinct_tag_keys(doc_coll)
        else:
            return self._document_store.list_distinct_tag_values(doc_coll, key)
    
    # -------------------------------------------------------------------------
    # Direct Access
    # -------------------------------------------------------------------------
    
    def get(self, id: str) -> Optional[Item]:
        """
        Retrieve a specific item by ID.

        Reads from document store (canonical), falls back to vector store for legacy data.
        Touches accessed_at on successful retrieval.
        """
        validate_id(id)
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Try document store first (canonical)
        try:
            doc_record = self._document_store.get(doc_coll, id)
        except Exception as e:
            logger.warning("DocumentStore.get(%s) failed: %s", id, e)
            if self._is_local and "malformed" in str(e):
                # SQLite-specific recovery — only for local backends
                if hasattr(self._document_store, '_try_runtime_recover'):
                    self._document_store._try_runtime_recover()
                # Retry once after recovery
                try:
                    doc_record = self._document_store.get(doc_coll, id)
                except Exception:
                    doc_record = None
            else:
                doc_record = None
        if doc_record:
            self._document_store.touch(doc_coll, id)
            return _record_to_item(doc_record)

        # Fall back to ChromaDB for legacy data
        result = self._store.get(chroma_coll, id)
        if result is None:
            return None
        return result.to_item()

    def get_version(
        self,
        id: str,
        offset: int = 0,
    ) -> Optional[Item]:
        """
        Get a specific version of a document by offset.

        Offset semantics:
        - 0 = current version
        - 1 = previous version
        - 2 = two versions ago
        - etc.

        Args:
            id: Document identifier
            offset: Version offset (0=current, 1=previous, etc.)

        Returns:
            Item if found, None if version doesn't exist
        """
        validate_id(id)
        doc_coll = self._resolve_doc_collection()

        if offset == 0:
            # Current version
            return self.get(id)

        # Get archived version
        version_info = self._document_store.get_version(doc_coll, id, offset)
        if version_info is None:
            return None

        return Item(
            id=id,
            summary=version_info.summary,
            tags=version_info.tags,
        )

    def list_versions(
        self,
        id: str,
        limit: int = 10,
    ) -> list[VersionInfo]:
        """
        List version history for a document.

        Returns versions in reverse chronological order (newest archived first).
        Does not include the current version.

        Args:
            id: Document identifier
            limit: Maximum versions to return

        Returns:
            List of VersionInfo, newest archived first
        """
        validate_id(id)
        doc_coll = self._resolve_doc_collection()
        return self._document_store.list_versions(doc_coll, id, limit)

    def get_version_nav(
        self,
        id: str,
        current_version: Optional[int] = None,
        limit: int = 3,
    ) -> dict[str, list[VersionInfo]]:
        """
        Get version navigation info (prev/next) for display.

        Args:
            id: Document identifier
            current_version: The version being viewed (None = current/live version)
            limit: Max previous versions to return when viewing current

        Returns:
            Dict with 'prev' and optionally 'next' lists of VersionInfo.
        """
        doc_coll = self._resolve_doc_collection()
        return self._document_store.get_version_nav(doc_coll, id, current_version, limit)

    def exists(self, id: str) -> bool:
        """
        Check if an item exists in the store.
        """
        validate_id(id)
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()
        # Check document store first, then ChromaDB
        return self._document_store.exists(doc_coll, id) or self._store.exists(chroma_coll, id)
    
    def delete(
        self,
        id: str,
        *,
        delete_versions: bool = True,
    ) -> bool:
        """
        Delete an item from both stores.

        Args:
            id: Document identifier
            delete_versions: If True, also delete version history

        Returns:
            True if item existed and was deleted.
        """
        validate_id(id)
        if is_part_id(id):
            raise ValueError(
                f"Cannot delete part directly: {id!r}. "
                "Use analyze() to replace parts, or delete the parent document."
            )
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()
        # Delete from both stores (including versions)
        doc_deleted = self._document_store.delete(doc_coll, id, delete_versions=delete_versions)
        chroma_deleted = self._store.delete(chroma_coll, id, delete_versions=delete_versions)
        return doc_deleted or chroma_deleted

    def revert(self, id: str) -> Optional[Item]:
        """
        Revert to the previous version, or delete if no versions exist.

        Returns the restored item, or None if the item was fully deleted.
        """
        validate_id(id)
        if is_part_id(id):
            raise ValueError(
                f"Cannot revert part directly: {id!r}. "
                "Use analyze() to replace parts, or delete the parent document."
            )
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        max_ver = self._document_store.max_version(doc_coll, id)

        if max_ver == 0:
            # No history — full delete
            self.delete(id)
            return None

        # Get the versioned ChromaDB ID we need to promote
        versioned_chroma_id = f"{id}@v{max_ver}"

        # Get the archived embedding from ChromaDB
        archived_embedding = self._store.get_embedding(chroma_coll, versioned_chroma_id)

        # Restore in DocumentStore (promotes latest version to current)
        restored = self._document_store.restore_latest_version(doc_coll, id)

        if restored is None:
            # Shouldn't happen given version_count > 0, but be safe
            self.delete(id)
            return None

        # Update ChromaDB: replace current with restored version's data
        if archived_embedding:
            self._store.upsert(
                collection=chroma_coll, id=id,
                embedding=archived_embedding,
                summary=restored.summary,
                tags=casefold_tags_for_index(restored.tags),
            )

        # Delete the versioned entry from ChromaDB
        self._store.delete_entries(chroma_coll, [versioned_chroma_id])

        # Clean up stale parts (structural decomposition of old content)
        self._store.delete_parts(chroma_coll, id)
        self._document_store.delete_parts(doc_coll, id)

        return self.get(id)

    # -------------------------------------------------------------------------
    # Current Working Context (Now)
    # -------------------------------------------------------------------------

    def get_now(self, *, scope: Optional[str] = None) -> Item:
        """
        Get the current working intentions.

        A singleton document representing what you're currently working on.
        If it doesn't exist, creates one with default content and tags from
        the bundled system now.md file.

        Args:
            scope: Optional scope for multi-user isolation (e.g. user ID).
                   When set, uses ``now:{scope}`` instead of the singleton ``now``.

        Returns:
            The current intentions Item (never None - auto-creates if missing)
        """
        doc_id = f"now:{scope}" if scope else NOWDOC_ID
        item = self.get(doc_id)
        if item is None:
            if scope:
                # Scoped now: initialize with minimal content
                item = self.set_now(f"# Now ({scope})\n\nWorking context.", scope=scope)
            else:
                # Singleton now: use bundled system doc
                try:
                    default_content, default_tags = _load_frontmatter(SYSTEM_DOC_DIR / "now.md")
                except FileNotFoundError:
                    default_content = "# Now\n\nYour working context."
                    default_tags = {}
                item = self.set_now(default_content, tags=default_tags)
        return item

    def set_now(
        self,
        content: str,
        *,
        scope: Optional[str] = None,
        tags: Optional[dict[str, str]] = None,
    ) -> Item:
        """
        Set the current working intentions.

        Updates the singleton intentions with new content. Uses put()
        internally with the fixed NOWDOC_ID.

        Args:
            content: New content for the current intentions
            scope: Optional scope for multi-user isolation (e.g. user ID).
                   When set, uses ``now:{scope}`` and auto-tags with ``user={scope}``.
            tags: Optional additional tags to apply

        Returns:
            The updated context Item
        """
        doc_id = f"now:{scope}" if scope else NOWDOC_ID
        merged_tags = dict(tags or {})
        if scope:
            merged_tags.setdefault("user", scope)
        return self.put(content, id=doc_id, tags=merged_tags or None)

    def move(
        self,
        name: str,
        *,
        source_id: str = NOWDOC_ID,
        tags: Optional[dict[str, str]] = None,
        only_current: bool = False,
    ) -> Item:
        """
        Move versions from a source document into a named item.

        Moves matching versions (filtered by tags if provided) from source_id
        to a named item. If the target already exists, extracted versions are
        appended to its history. The source retains non-matching versions;
        if fully emptied and source is 'now', it resets to default.

        Args:
            name: ID for the target item (created if new, extended if exists)
            source_id: Document to extract from (default: now)
            tags: If provided, only extract versions whose tags contain
                  all specified key=value pairs. If None, extract all.
            only_current: If True, only extract the current (tip) version,
                        not any archived history.

        Returns:
            The moved Item.

        Raises:
            ValueError: If name is empty, source doesn't exist,
                        or no versions match the filter.
        """
        if not name:
            raise ValueError("Name cannot be empty")
        validate_id(name)
        validate_id(source_id)
        if is_part_id(name):
            raise ValueError(
                f"Cannot move to a part ID: {name!r}. "
                "Parts are managed by analyze()."
            )

        # Casefold tag filters so they match casefolded storage
        if tags:
            tags = casefold_tags(tags)

        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Get the source's archived version numbers before extraction
        # (needed to map to ChromaDB versioned IDs)
        source_versions = self._document_store.list_versions(
            doc_coll, source_id, limit=10000
        )
        source_current = self._document_store.get(doc_coll, source_id)
        if source_current is None:
            raise ValueError(f"Source document '{source_id}' not found")

        # Identify which versions will be extracted (for ChromaDB cleanup)
        def _tags_match(item_tags: dict, filt: dict) -> bool:
            for k, v in filt.items():
                stored = item_tags.get(k)
                if stored is None or stored.casefold() != v.casefold():
                    return False
            return True

        if only_current:
            # Only extract the tip — no archived versions
            matched_version_nums = []
            if tags:
                current_matches = _tags_match(source_current.tags, tags)
            else:
                current_matches = True
        elif tags:
            matched_version_nums = [
                v.version for v in source_versions
                if _tags_match(v.tags, tags)
            ]
            current_matches = _tags_match(source_current.tags, tags)
        else:
            matched_version_nums = [v.version for v in source_versions]
            current_matches = True

        # Extract in DocumentStore (SQLite side)
        extracted, new_source, base_version = self._document_store.extract_versions(
            doc_coll, source_id, name, tag_filter=tags,
            only_current=only_current,
        )

        # ChromaDB side: move embeddings

        # 1. Collect source ChromaDB IDs for matched versions
        source_chroma_ids = [f"{source_id}@v{n}" for n in matched_version_nums]
        if current_matches:
            source_chroma_ids.append(source_id)

        # 2. If target already exists, archive its current embedding
        # (extract_versions already archived the SQLite side; we mirror in ChromaDB)
        if base_version > 1:
            archive_version = base_version - 1  # version assigned by _archive_current_unlocked
            existing = self._store.get_entries_full(chroma_coll, [name])
            if existing and existing[0].get("embedding") is not None:
                entry = existing[0]
                archived_vid = f"{name}@v{archive_version}"
                archived_tags = dict(entry["tags"])
                archived_tags["_version"] = str(archive_version)
                archived_tags["_base_id"] = name
                self._store.upsert_batch(
                    chroma_coll,
                    [archived_vid],
                    [entry["embedding"]],
                    [entry["summary"] or ""],
                    [casefold_tags_for_index(archived_tags)],
                )

        # 3. Batch-get embeddings from source
        embedding_map: dict[str, list[float]] = {}
        if source_chroma_ids:
            source_entries = self._store.get_entries_full(chroma_coll, source_chroma_ids)
            for entry in source_entries:
                if entry.get("embedding") is not None:
                    embedding_map[entry["id"]] = entry["embedding"]

            # 4. Batch-delete source entries
            found_ids = [entry["id"] for entry in source_entries]
            if found_ids:
                self._store.delete_entries(chroma_coll, found_ids)

        # 5. Insert target entries with new IDs
        # The extracted list is chronological (oldest first),
        # last one is current, rest are history
        target_ids = []
        target_embeddings = []
        target_summaries = []
        target_tags = []

        # History versions (sequential from base_version)
        for seq, vi in enumerate(extracted[:-1], start=base_version):
            target_vid = f"{name}@v{seq}"
            # Find the embedding for this version
            source_vid = f"{source_id}@v{vi.version}" if vi.version > 0 else source_id
            emb = embedding_map.get(source_vid)
            if emb is not None:
                ver_tags = dict(vi.tags)
                ver_tags["_version"] = str(seq)
                ver_tags["_base_id"] = name
                target_ids.append(target_vid)
                target_embeddings.append(emb)
                target_summaries.append(vi.summary)
                target_tags.append(ver_tags)

        # Current (newest extracted)
        newest = extracted[-1]
        source_cur_id = f"{source_id}@v{newest.version}" if newest.version > 0 else source_id
        cur_emb = embedding_map.get(source_cur_id)
        if cur_emb is not None:
            cur_tags = dict(newest.tags)
            cur_tags["_saved_from"] = source_id
            from .types import utc_now as _utc_now
            cur_tags["_saved_at"] = _utc_now()
            target_ids.append(name)
            target_embeddings.append(cur_emb)
            target_summaries.append(newest.summary)
            target_tags.append(cur_tags)

        # 6. Batch-insert/update into ChromaDB
        if target_ids:
            self._store.upsert_batch(
                chroma_coll,
                target_ids,
                target_embeddings,
                target_summaries,
                [casefold_tags_for_index(t) for t in target_tags],
            )

        # Add system tags to the saved document in DocumentStore too
        saved_doc = self._document_store.get(doc_coll, name)
        if saved_doc:
            saved_tags = dict(saved_doc.tags)
            saved_tags["_saved_from"] = source_id
            from .types import utc_now as _utc_now2
            saved_tags["_saved_at"] = _utc_now2()
            self._document_store.update_tags(doc_coll, name, saved_tags)

        # If source was fully emptied and is 'now', recreate with defaults
        if new_source is None and source_id == NOWDOC_ID:
            try:
                default_content, default_tags = _load_frontmatter(
                    SYSTEM_DOC_DIR / "now.md"
                )
            except FileNotFoundError:
                default_content = "# Now\n\nYour working context."
                default_tags = {}
            self.set_now(default_content, tags=default_tags)

        return self.get(name)

    def reset_system_documents(self) -> dict:
        """Force reload all system documents from bundled content."""
        from .system_docs import reset_system_documents
        return reset_system_documents(self)

    def tag(
        self,
        id: str,
        tags: Optional[dict[str, str]] = None,
    ) -> Optional[Item]:
        """
        Update tags on an existing document without re-processing.

        Does NOT re-fetch, re-embed, or re-summarize. Only updates tags.

        Tag behavior:
        - Provided tags are merged with existing user tags
        - Empty string value ("") deletes that tag
        - System tags (_prefixed) cannot be modified via this method

        Args:
            id: Document identifier
            tags: Tags to add/update/delete (empty string = delete)

        Returns:
            Updated Item if found, None if document doesn't exist
        """
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Validate inputs
        validate_id(id)
        if tags:
            # Casefold user tags on write
            tags = casefold_tags(tags)
            for key, value in tags.items():
                if not key.startswith(SYSTEM_TAG_PREFIX):
                    validate_tag_key(key)
                    if len(value) > MAX_TAG_VALUE_LENGTH:
                        raise ValueError(f"Tag value too long (max {MAX_TAG_VALUE_LENGTH}): {key!r}")
            # Validate constrained tags
            self._validate_constrained_tags(
                {k: v for k, v in tags.items()
                 if not k.startswith(SYSTEM_TAG_PREFIX) and v != ""}
            )

        # Get existing item (prefer document store, fall back to ChromaDB)
        existing = self.get(id)
        if existing is None:
            return None

        # Start with existing tags, separate system from user
        current_tags = dict(existing.tags)
        system_tags = {k: v for k, v in current_tags.items()
                       if k.startswith(SYSTEM_TAG_PREFIX)}
        user_tags = {k: v for k, v in current_tags.items()
                     if not k.startswith(SYSTEM_TAG_PREFIX)}

        # Apply tag changes (filter out system tags from input)
        if tags:
            for key, value in tags.items():
                if key.startswith(SYSTEM_TAG_PREFIX):
                    continue  # Cannot modify system tags
                if value == "":
                    # Empty string = delete
                    user_tags.pop(key, None)
                else:
                    user_tags[key] = value

        # Merge back: user tags + system tags
        final_tags = {**user_tags, **system_tags}

        # Dual-write: SQLite gets original values, ChromaDB gets casefolded
        self._document_store.update_tags(doc_coll, id, final_tags)
        self._store.update_tags(chroma_coll, id, casefold_tags_for_index(final_tags))

        # Return updated item
        return self.get(id)

    def tag_part(
        self,
        id: str,
        part_num: int,
        tags: Optional[dict[str, str]] = None,
    ) -> Optional[PartInfo]:
        """
        Update user tags on a part without re-analyzing.

        Parts are machine-generated, so summaries and content are immutable.
        Tags can be edited to correct or override analyzer tagging decisions.

        System tags (_prefixed) cannot be modified. Empty string deletes a tag.

        Args:
            id: Parent document ID (not the part ID)
            part_num: Part number (1-indexed)
            tags: Tags to add/update/delete (empty string = delete)

        Returns:
            Updated PartInfo if found, None if part doesn't exist
        """
        validate_id(id)
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        part = self._document_store.get_part(doc_coll, id, part_num)
        if part is None:
            return None

        # Merge: existing tags + new tags, empty string = delete
        merged = dict(part.tags)
        if tags:
            tags = casefold_tags(tags)
            for key, value in tags.items():
                if key.startswith(SYSTEM_TAG_PREFIX):
                    continue  # skip system tags
                validate_tag_key(key)
                if len(value) > MAX_TAG_VALUE_LENGTH:
                    raise ValueError(f"Tag value too long (max {MAX_TAG_VALUE_LENGTH}): {key!r}")
                if value == "":
                    merged.pop(key, None)
                else:
                    merged[key] = value
            self._validate_constrained_tags(
                {k: v for k, v in tags.items()
                 if not k.startswith(SYSTEM_TAG_PREFIX) and v != ""}
            )

        # Dual-write: SQLite gets original values, ChromaDB gets casefolded
        self._document_store.update_part_tags(doc_coll, id, part_num, merged)
        self._store.update_tags(chroma_coll, f"{id}@p{part_num}",
                                casefold_tags_for_index(merged))

        return self._document_store.get_part(doc_coll, id, part_num)

    # -------------------------------------------------------------------------
    # Parts (structural decomposition)
    # -------------------------------------------------------------------------

    def analyze(
        self,
        id: str,
        *,
        analyzer=None,
        tags: Optional[list[str]] = None,
        force: bool = False,
    ) -> list[PartInfo]:
        """
        Decompose a note or string into meaningful parts.

        For URI-sourced documents: decomposes the document content structurally.
        For inline notes (strings): assembles the version history and decomposes
        the temporal sequence into episodic parts.

        Uses a pluggable AnalyzerProvider to identify sections with summaries
        and tags. Re-analysis replaces all previous parts atomically.

        Skips analysis if the document's content_hash matches the stored
        _analyzed_hash tag (parts are already current). Use force=True
        to override.

        Args:
            id: Document or string to analyze
            analyzer: Override AnalyzerProvider for decomposition
                (default: use configured analyzer or SlidingWindowAnalyzer)
            tags: Guidance tag keys (e.g., ["topic", "type"]) —
                fetches .tag/xxx descriptions as decomposition context
            force: Skip the _analyzed_hash check and re-analyze regardless

        Returns:
            List of PartInfo for the created parts (empty list if skipped)
        """
        from .providers.base import AnalysisChunk

        validate_id(id)
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Get the document
        doc_record = self._document_store.get(doc_coll, id)
        if doc_record is None:
            raise ValueError(f"Document not found: {id}")

        # Skip if parts are already current (content unchanged since last analysis)
        if not force and doc_record.content_hash:
            analyzed_hash = doc_record.tags.get("_analyzed_hash")
            if analyzed_hash and analyzed_hash == doc_record.content_hash:
                logger.info("Skipping analysis for %s: parts already current", id)
                return self.list_parts(id)

        # Build AnalysisChunks from content.
        # For URI sources: re-fetch → single chunk.
        # For inline sources (strings): version history → one chunk per version.
        chunks: list[AnalysisChunk] = []
        source = doc_record.tags.get("_source")
        parent_user_tags = {
            k: v for k, v in doc_record.tags.items()
            if not k.startswith(SYSTEM_TAG_PREFIX)
        }

        if source == "uri":
            try:
                doc = self._document_provider.fetch(id)
                chunks = [AnalysisChunk(
                    content=doc.content,
                    tags=parent_user_tags,
                    index=0,
                )]
            except Exception as e:
                logger.warning("Could not re-fetch %s: %s, using summary", id, e)

        if not chunks:
            # For inline notes, build chunks from version history
            versions = self._document_store.list_versions(doc_coll, id, limit=100)
            if versions:
                # Chronological order (oldest first)
                for i, v in enumerate(reversed(versions)):
                    date_str = v.created_at[:10] if v.created_at else ""
                    chunks.append(AnalysisChunk(
                        content=f"[{date_str}]\n{v.summary}",
                        tags=parent_user_tags,
                        index=i,
                    ))
                # Current version as newest
                chunks.append(AnalysisChunk(
                    content=f"[current]\n{doc_record.summary}",
                    tags=parent_user_tags,
                    index=len(chunks),
                ))
            else:
                chunks = [AnalysisChunk(
                    content=doc_record.summary,
                    tags=parent_user_tags,
                    index=0,
                )]

        # Validate minimum content
        total_content = "".join(c.content for c in chunks)
        if len(total_content.strip()) < 50:
            raise ValueError(f"Document content too short to analyze: {id}")

        # Build guide context from tag descriptions
        guide_context = ""
        if tags:
            guide_parts = []
            for tag_key in tags:
                tag_doc_id = f".tag/{tag_key}"
                tag_doc = self._document_store.get(doc_coll, tag_doc_id)
                if tag_doc:
                    guide_parts.append(
                        f"## Tag: {tag_key}\n{tag_doc.summary}"
                    )
            if guide_parts:
                guide_context = "\n\n".join(guide_parts)

        # Get the analyzer.
        # Wait for any background reconciliation to finish first — both
        # sentence-transformers (embedding) and mlx-lm (summarization)
        # import the `transformers` package, and concurrent imports
        # corrupt module state (Python import lock is per-module).
        if analyzer is None:
            self._reconcile_done.wait(timeout=30)
            analyzer = self._get_analyzer()

        # Delegate to the analyzer
        raw_parts = analyzer.analyze(chunks, guide_context)

        # Content not decomposable — single section is redundant with the note
        if len(raw_parts) <= 1:
            logger.info("Content not decomposable into multiple parts: %s", id)
            return []

        # Post-analysis classification: if constrained tag specs exist in the
        # store, classify parts against the taxonomy (one extra LLM call).
        try:
            from .analyzers import TagClassifier
            classifier = TagClassifier(
                provider=self._get_summarization_provider(),
            )
            specs = classifier.load_specs(self)
            if specs:
                classifier.classify(raw_parts, specs)
                logger.info("Classified %d parts against %d tag specs", len(raw_parts), len(specs))
        except Exception as e:
            logger.warning("Tag classification skipped: %s", e)

        # Build PartInfo list
        from .types import utc_now
        now = utc_now()

        parts: list[PartInfo] = []
        for i, raw in enumerate(raw_parts, 1):
            part_tags = dict(parent_user_tags)
            # Merge part-specific tags from LLM
            if raw.get("tags"):
                part_tags.update(raw["tags"])

            part_content = raw.get("content", "")
            part_summary = raw.get("summary", part_content[:200])

            parts.append(PartInfo(
                part_num=i,
                summary=part_summary,
                tags=part_tags,
                content=part_content,
                created_at=now,
            ))

        # Delete existing parts (re-analysis = fresh decomposition)
        self._store.delete_parts(chroma_coll, id)
        self._document_store.delete_parts(doc_coll, id)

        # Store parts in document store
        self._document_store.upsert_parts(doc_coll, id, parts)

        # NOTE: Previously released summarization model here to avoid both
        # MLX + embedding models being resident simultaneously. However,
        # releasing causes MLX to leak ~40MB per reload cycle (Metal allocator
        # doesn't return pages to OS). Keeping the model resident is better —
        # total footprint is ~3GB (MLX 1.8GB + ST 0.5GB + overhead) which
        # fits on 16GB+ machines. On low-memory systems, consider subprocess
        # isolation instead.
        # self._release_summarization_provider()

        # Generate embeddings and store in vector store
        embed = self._get_embedding_provider()
        for part in parts:
            embedding = embed.embed(part.summary)
            self._store.upsert_part(
                chroma_coll, id, part.part_num,
                embedding, part.summary, casefold_tags_for_index(part.tags),
            )

        # Record the content hash at which analysis was performed,
        # so future calls can skip if content hasn't changed.
        if doc_record.content_hash:
            updated_tags = dict(doc_record.tags)
            updated_tags["_analyzed_hash"] = doc_record.content_hash
            self._document_store.update_tags(doc_coll, id, updated_tags)
            self._store.update_tags(chroma_coll, id,
                                    casefold_tags_for_index(updated_tags))

        return parts

    def get_part(self, id: str, part_num: int) -> Optional[Item]:
        """
        Get a specific part of a document.

        Returns the part as an Item with _part_num, _base_id, and
        _total_parts metadata tags.

        Args:
            id: Document identifier
            part_num: Part number (1-indexed)

        Returns:
            Item if found, None otherwise
        """
        doc_coll = self._resolve_doc_collection()
        part = self._document_store.get_part(doc_coll, id, part_num)
        if part is None:
            return None

        total = self._document_store.part_count(doc_coll, id)
        tags = dict(part.tags)
        tags["_part_num"] = str(part.part_num)
        tags["_base_id"] = id
        tags["_total_parts"] = str(total)

        return Item(
            id=id,
            summary=part.content if part.content else part.summary,
            tags=tags,
        )

    def list_parts(self, id: str) -> list[PartInfo]:
        """
        List all parts for a document.

        Args:
            id: Document identifier

        Returns:
            List of PartInfo, ordered by part_num
        """
        doc_coll = self._resolve_doc_collection()
        return self._document_store.list_parts(doc_coll, id)

    # -------------------------------------------------------------------------
    # Collection Management
    # -------------------------------------------------------------------------

    def list_collections(self) -> list[str]:
        """
        List all collections in the store.
        """
        # Merge collections from both stores
        doc_collections = set(self._document_store.list_collections())
        chroma_collections = set(self._store.list_collections())
        return sorted(doc_collections | chroma_collections)
    
    def count(self) -> int:
        """
        Count items in a collection.

        Returns count from document store if available, else vector store.
        """
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()
        doc_count = self._document_store.count(doc_coll)
        if doc_count > 0:
            return doc_count
        return self._store.count(chroma_coll)

    def count_versions(self) -> int:
        """Count archived versions in the collection."""
        doc_coll = self._resolve_doc_collection()
        return self._document_store.count_versions(doc_coll)

    def list_items(
        self,
        *,
        prefix: Optional[str] = None,
        tags: Optional[dict[str, str]] = None,
        tag_keys: Optional[list[str]] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        order_by: str = "updated",
        include_hidden: bool = False,
        include_history: bool = False,
        limit: int = 10,
    ) -> list[Item]:
        """
        List items with composable filters.

        All filters are AND'd together. Prefix narrows by ID, tags narrow
        by metadata, date narrows by time.

        Args:
            prefix: ID prefix filter (e.g. ".tag/act").
            tags: Tag key=value filters (all must match).
            tag_keys: Tag key-only filters (item must have key, any value).
            since: Only items updated since (ISO duration like P3D, or date).
            until: Only items updated before (ISO duration or date).
            order_by: Sort order - "updated" (default) or "accessed".
            include_hidden: Include system notes (dot-prefix IDs).
            include_history: Include previous versions alongside current items.
            limit: Maximum results to return.

        Returns:
            List of Items, most recent first.
        """
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Casefold tag filters to match casefolded storage
        if tags:
            tags = {k.casefold(): v.casefold() for k, v in tags.items()}
            for k in tags:
                validate_tag_key(k)
        if tag_keys:
            tag_keys = [k.casefold() for k in tag_keys]
            for k in tag_keys:
                validate_tag_key(k)

        # Fetch extra to allow room for post-filtering
        fetch_limit = limit * 3

        # ------------------------------------------------------------------
        # Primary data source: pick the most selective query
        # ------------------------------------------------------------------
        if tags:
            # Key=value tags: use ChromaDB metadata query
            where_conditions = [{k: v} for k, v in tags.items()]
            if len(where_conditions) == 1:
                where = where_conditions[0]
            else:
                where = {"$and": where_conditions}
            results = self._store.query_metadata(chroma_coll, where, limit=fetch_limit)
            # Enrich from SQLite for original-case tag values
            items = []
            for r in results:
                doc = self._document_store.get(doc_coll, r.id)
                if doc:
                    items.append(_record_to_item(doc))
                else:
                    items.append(r.to_item())

        elif tag_keys:
            # Key-only: use SQLite tag key query (first key as primary,
            # additional keys as post-filter)
            since_date = _parse_date_param(since) if since else None
            until_date = _parse_date_param(until) if until else None
            docs = self._document_store.query_by_tag_key(
                doc_coll, tag_keys[0], limit=fetch_limit,
                since_date=since_date, until_date=until_date,
            )
            items = [_record_to_item(d) for d in docs]
            # Post-filter additional key-only tags
            for extra_key in tag_keys[1:]:
                items = [i for i in items if extra_key in i.tags]

        elif prefix is not None:
            # ID prefix query
            pfx = prefix.rstrip("/") + "/"
            records = self._document_store.query_by_id_prefix(doc_coll, pfx)
            items = [_record_to_item(rec) for rec in records]
            # Also include the parent doc itself
            parent = self._document_store.get(doc_coll, prefix)
            if parent and not any(i.id == prefix for i in items):
                items.insert(0, _record_to_item(parent))

        else:
            # Default: recent items
            if include_history:
                records = self._document_store.list_recent_with_history(
                    doc_coll, fetch_limit, order_by=order_by,
                )
            else:
                records = self._document_store.list_recent(
                    doc_coll, fetch_limit, order_by=order_by,
                )
            items = [_record_to_item(rec) for rec in records]

        # ------------------------------------------------------------------
        # Post-filters (apply remaining predicates)
        # ------------------------------------------------------------------

        # Prefix filter (if primary query wasn't prefix-based)
        if prefix is not None and tags:
            pfx = prefix.rstrip("/") + "/"
            items = [i for i in items if i.id.startswith(pfx) or i.id == prefix]

        # Tag-key filter (if primary query was something else)
        if tag_keys and tags:
            for k in tag_keys:
                items = [i for i in items if k in i.tags]

        # Date filter (skip if already applied in SQL via tag_keys path)
        if (since is not None or until is not None) and not tag_keys:
            items = _filter_by_date(items, since=since, until=until)

        # Hidden filter (prefix queries always include hidden)
        if not include_hidden and prefix is None:
            items = [i for i in items if not _is_hidden(i)]

        return items[:limit]

    # -------------------------------------------------------------------------
    # Pending Work Queue (summaries + analysis)
    # -------------------------------------------------------------------------

    def enqueue_analyze(
        self,
        id: str,
        tags: Optional[list[str]] = None,
        force: bool = False,
    ) -> bool:
        """
        Enqueue a note for background analysis (decomposition into parts).

        Validates the document exists, then adds it to the pending work
        queue for serial processing by the background daemon.

        Skips enqueueing if the document's _analyzed_hash matches its
        content_hash (parts are already current). Use force=True to
        override.

        Args:
            id: Document ID to analyze
            tags: Guidance tag keys for decomposition
            force: Enqueue even if parts are already current

        Returns:
            True if enqueued, False if skipped (parts already current)
        """
        validate_id(id)
        doc_coll = self._resolve_doc_collection()
        doc = self._document_store.get(doc_coll, id)
        if doc is None:
            raise ValueError(f"Document not found: {id}")

        # Skip if parts are already current
        if not force and doc.content_hash:
            analyzed_hash = doc.tags.get("_analyzed_hash")
            if analyzed_hash and analyzed_hash == doc.content_hash:
                logger.info("Skipping enqueue for %s: parts already current", id)
                return False

        metadata = {}
        if tags:
            metadata["tags"] = tags
        if force:
            metadata["force"] = True

        self._pending_queue.enqueue(
            id, doc_coll, "",
            task_type="analyze",
            metadata=metadata,
        )
        self._spawn_processor()
        return True

    def process_pending(self, limit: int = 10) -> dict:
        """
        Process pending work items (embedding, summaries, and analysis).

        Handles three task types serially:
        - "embed": computes and stores embeddings (cloud mode deferred writes)
        - "summarize": generates real summaries for lazy-indexed items
        - "analyze": decomposes documents into parts via LLM

        Items that fail MAX_SUMMARY_ATTEMPTS times are removed from
        the queue.

        Args:
            limit: Maximum number of items to process in this batch

        Returns:
            Dict with: processed (int), failed (int), abandoned (int), errors (list)
        """
        items = self._pending_queue.dequeue(limit=limit)
        result = {"processed": 0, "failed": 0, "abandoned": 0, "errors": []}

        for item in items:
            # Skip items that have failed too many times
            # (attempts was already incremented by dequeue, so check >= MAX)
            if item.attempts >= MAX_SUMMARY_ATTEMPTS:
                # Give up - remove from queue
                self._pending_queue.complete(
                    item.id, item.collection, item.task_type
                )
                result["abandoned"] += 1
                logger.warning(
                    "Abandoned pending %s after %d attempts: %s",
                    item.task_type, item.attempts, item.id
                )
                continue

            try:
                _task_verbs = {
                    "analyze": "Analyzing",
                    "embed": "Embedding",
                    "reindex": "Re-embedding",
                    "summarize": "Summarizing",
                }
                verb = _task_verbs.get(item.task_type, item.task_type)
                logger.info("%s %s (attempt %d)", verb, item.id, item.attempts)
                if item.task_type == "analyze":
                    self._process_pending_analyze(item)
                    # analyze releases summarization internally;
                    # release embedding after parts are embedded
                    self._release_embedding_provider()
                elif item.task_type == "embed":
                    self._process_pending_embed(item)
                    self._release_embedding_provider()
                elif item.task_type == "reindex":
                    self._process_pending_reindex(item)
                    self._release_embedding_provider()
                else:
                    self._process_pending_summarize(item)
                    # Release summarization model between items to
                    # prevent both models residing in memory at once
                    self._release_summarization_provider()

                # Remove from queue
                self._pending_queue.complete(
                    item.id, item.collection, item.task_type
                )
                result["processed"] += 1
                logger.info("%s %s done", verb, item.id)

                # Brief yield between items so interactive processes
                # (keep now, keep find) can acquire the model lock
                # without waiting for the entire batch to finish.
                time.sleep(0.1)

            except Exception as e:
                # Mark as failed so it resets to pending for retry.
                # (attempt counter already incremented by dequeue)
                error_msg = f"{type(e).__name__}: {e}"
                self._pending_queue.fail(
                    item.id, item.collection, item.task_type,
                    error=error_msg,
                )
                result["failed"] += 1
                result["errors"].append(f"{item.id}: {error_msg}")
                logger.warning("Failed to %s %s (attempt %d): %s",
                             item.task_type, item.id, item.attempts, e)

        return result

    def _process_pending_summarize(self, item) -> None:
        """Process a pending summarization work item."""
        # Get item's tags for contextual summarization
        doc = self._document_store.get(item.collection, item.id)
        context = None
        if doc:
            # Filter to user tags (non-system)
            user_tags = filter_non_system_tags(doc.tags)
            if user_tags:
                context = self._gather_context(
                    item.id, user_tags
                )

        # Generate real summary (with optional context)
        summary = self._get_summarization_provider().summarize(
            item.content, context=context
        )

        # Update summary in both stores
        self._document_store.update_summary(item.collection, item.id, summary)
        self._store.update_summary(item.collection, item.id, summary)

    def _process_pending_embed(self, item) -> None:
        """Process a deferred embedding task (cloud mode).

        Computes the embedding for the content and writes it to the
        vector store.  If the doc's content changed (metadata flag),
        archives the old embedding as a versioned entry first.
        """
        doc_coll = item.collection
        chroma_coll = self._resolve_chroma_collection()

        # Get current doc record (may have been deleted before we got here)
        doc = self._document_store.get(doc_coll, item.id)
        if doc is None:
            return

        content_changed = (item.metadata or {}).get("content_changed", False)

        # Archive old embedding before overwriting (version archival)
        if content_changed:
            old_embedding = self._store.get_embedding(chroma_coll, item.id)
            max_ver = self._document_store.max_version(doc_coll, item.id)
            if max_ver > 0 and old_embedding is not None:
                # Get the archived version's metadata for the versioned entry
                archived = self._document_store.get_version(
                    doc_coll, item.id, offset=1
                )
                if archived:
                    self._store.upsert_version(
                        collection=chroma_coll,
                        id=item.id,
                        version=max_ver,
                        embedding=old_embedding,
                        summary=archived.summary,
                        tags=casefold_tags_for_index(archived.tags),
                    )

        # Compute embedding (try dedup first)
        embedding = self._try_dedup_embedding(
            doc_coll, chroma_coll, doc.content_hash, item.id, item.content,
        )
        if embedding is None:
            embedding = self._get_embedding_provider().embed(item.content)

        # Write to vector store
        self._store.upsert(
            collection=chroma_coll,
            id=item.id,
            embedding=embedding,
            summary=doc.summary,
            tags=casefold_tags_for_index(doc.tags),
        )

    def _process_pending_analyze(self, item) -> None:
        """Process a pending analysis work item."""
        tags = item.metadata.get("tags") if item.metadata else None
        force = item.metadata.get("force", False) if item.metadata else False
        parts = self.analyze(item.id, tags=tags, force=force)
        logger.info("Analyzed %s into %d parts", item.id, len(parts))

    def _process_pending_reindex(self, item) -> None:
        """Process a reindex task: embed summary and write to vector store.

        Handles both main docs and versioned entries.
        The item.content contains the summary text to embed.
        """
        chroma_coll = self._resolve_chroma_collection()
        meta = item.metadata or {}
        version = meta.get("version")
        base_id = meta.get("base_id")

        embedding = self._get_embedding_provider().embed(item.content)

        if version is not None and base_id is not None:
            # Versioned entry
            self._store.upsert_version(
                collection=chroma_coll,
                id=base_id,
                version=version,
                embedding=embedding,
                summary=item.content,
                tags=casefold_tags_for_index(meta.get("tags", {})),
            )
        else:
            # Main doc entry — use fresh tags from doc store
            doc = self._document_store.get(item.collection, item.id)
            if doc is None:
                return  # deleted since enqueue
            self._store.upsert(
                collection=chroma_coll,
                id=item.id,
                embedding=embedding,
                summary=item.content,
                tags=casefold_tags_for_index(doc.tags),
            )

    def pending_count(self) -> int:
        """Get count of pending summaries awaiting processing."""
        return self._pending_queue.count()

    def pending_stats(self) -> dict:
        """
        Get pending summary queue statistics.

        Returns dict with: pending, collections, max_attempts, oldest, queue_path, by_type
        """
        return self._pending_queue.stats()

    def pending_stats_by_type(self) -> dict[str, int]:
        """Get pending queue counts grouped by task type."""
        return self._pending_queue.stats_by_type()

    def pending_status(self, id: str) -> Optional[dict]:
        """
        Get pending task status for a specific note.

        Returns dict with id, task_type, status, queued_at if the note
        has pending work, or None if no work is pending. Requires a
        queue implementation that supports get_status().
        """
        return self._pending_queue.get_status(id)

    @property
    def _processor_pid_path(self) -> Path:
        """Path to the processor PID file."""
        return self._store_path / "processor.pid"

    def _is_processor_running(self) -> bool:
        """Check if a processor is already running via lock probe."""
        from .model_lock import ModelLock

        lock = ModelLock(self._store_path / ".processor.lock")
        return lock.is_locked()

    def _spawn_processor(self) -> bool:
        """
        Spawn a background processor if not already running.

        Uses an exclusive file lock to prevent TOCTOU race conditions
        where two processes could both check, find no processor, and
        both spawn one.

        Returns True if a new processor was spawned, False if one was
        already running or spawn failed.
        """
        from .model_lock import ModelLock

        spawn_lock = ModelLock(self._store_path / ".processor_spawn.lock")

        # Non-blocking: if another process is already spawning, let it handle it
        if not spawn_lock.acquire(blocking=False):
            return False

        log_fd = None
        try:
            if self._is_processor_running():
                return False

            # Spawn detached process
            # Use sys.executable to ensure we use the same Python
            cmd = [
                sys.executable, "-m", "keep.cli",
                "pending",
                "--daemon",
                "--store", str(self._store_path),
            ]

            # Platform-specific detachment
            # Redirect daemon stderr to ops log for crash diagnostics
            log_path = self._store_path / "keep-ops.log"
            try:
                log_fd = open(log_path, "a")
            except OSError:
                log_fd = None
            kwargs: dict = {
                "stdout": subprocess.DEVNULL,
                "stderr": log_fd if log_fd else subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }

            if sys.platform != "win32":
                # Unix: start new session to fully detach
                kwargs["start_new_session"] = True
            else:
                # Windows: use CREATE_NEW_PROCESS_GROUP
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

            subprocess.Popen(cmd, **kwargs)
            logger.info("Spawned background processor")
            return True

        except Exception as e:
            # Spawn failed - log for debugging, queue will be processed later
            logger.warning("Failed to spawn background processor: %s", e)
            return False
        finally:
            # Close parent's copy of the log fd (child inherited it)
            if log_fd:
                log_fd.close()
            spawn_lock.release()

    def reconcile(
        self,
        fix: bool = False,
    ) -> dict:
        """
        Check and optionally fix consistency between document store and vector store.

        Detects:
        - Documents in document store missing from vector store (not searchable)
        - Documents in vector store missing from document store (orphaned embeddings)

        Args:
            fix: If True, re-index documents missing from vector store

        Returns:
            Dict with 'missing_from_index', 'orphaned_in_index', 'fixed' counts
        """
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Find mismatches between stores
        doc_ids = self._document_store.list_ids(doc_coll)
        missing_from_chroma = self._store.find_missing_ids(chroma_coll, doc_ids)

        # Skip versioned (@v{N}) and part (@p{N}) IDs — tracked in separate tables
        chroma_ids = self._store.list_ids(chroma_coll)
        doc_id_set = set(doc_ids)
        orphaned_in_chroma = {
            cid for cid in chroma_ids
            if cid not in doc_id_set and "@v" not in cid and "@p" not in cid
        }

        fixed = 0
        removed = 0
        if fix:
            # Re-index items missing from ChromaDB using stored summary
            for doc_id in missing_from_chroma:
                try:
                    doc_record = self._document_store.get(doc_coll, doc_id)
                    if doc_record:
                        embedding = self._get_embedding_provider().embed(doc_record.summary)
                        self._store.upsert(
                            collection=chroma_coll,
                            id=doc_id,
                            embedding=embedding,
                            summary=doc_record.summary,
                            tags=casefold_tags_for_index(doc_record.tags),
                        )
                        fixed += 1
                        logger.info("Reconciled: %s", doc_id)
                except Exception as e:
                    logger.warning("Failed to reconcile %s: %s", doc_id, e)

            # Remove orphaned ChromaDB entries
            for orphan_id in orphaned_in_chroma:
                try:
                    self._store.delete(chroma_coll, orphan_id)
                    removed += 1
                    logger.info("Removed orphan: %s", orphan_id)
                except Exception as e:
                    logger.warning("Failed to remove orphan %s: %s", orphan_id, e)

        return {
            "missing_from_index": len(missing_from_chroma),
            "orphaned_in_index": len(orphaned_in_chroma),
            "fixed": fixed,
            "removed": removed,
            "missing_ids": list(missing_from_chroma) if missing_from_chroma else [],
            "orphaned_ids": list(orphaned_in_chroma) if orphaned_in_chroma else [],
        }

    @property
    def config(self) -> "StoreConfig":
        """Public access to store configuration."""
        return self._config

    def close(self) -> None:
        """
        Close resources (stores, caches, queues).

        Releases model locks (freeing GPU memory) before releasing file locks,
        ensuring the next process gets a clean GPU.
        """
        # Release locked model providers (frees GPU memory + gc)
        self._release_embedding_provider()
        self._release_summarization_provider()

        if self._media_describer is not None:
            if hasattr(self._media_describer, 'release'):
                self._media_describer.release()
            self._media_describer = None

        # Close ChromaDB store
        if hasattr(self, '_store') and self._store is not None:
            self._store.close()

        # Close document store (SQLite)
        if hasattr(self, '_document_store') and self._document_store is not None:
            self._document_store.close()

        # Close pending summary queue
        if hasattr(self, '_pending_queue'):
            self._pending_queue.close()

        # Remove ops log handler to avoid handler accumulation
        if hasattr(self, '_ops_log_handler') and self._ops_log_handler:
            import logging
            logging.getLogger("keep").removeHandler(self._ops_log_handler)
            self._ops_log_handler.close()
            self._ops_log_handler = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close resources."""
        self.close()
        return False

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            pass  # Suppress errors during garbage collection
