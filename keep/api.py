"""
Core API for reflective memory.

This is the minimal working implementation focused on:
- update(): fetch → embed → summarize → store
- remember(): embed → summarize → store  
- find(): embed query → search
- get(): retrieve by ID
"""

import hashlib
import importlib.resources
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _parse_since(since: str) -> str:
    """
    Parse a 'since' string and return a YYYY-MM-DD cutoff date.

    Accepts:
    - ISO 8601 duration: P3D (3 days), P1W (1 week), PT1H (1 hour), P1DT12H, etc.
    - ISO date: 2026-01-15
    - Date with slashes: 2026/01/15

    Returns:
        YYYY-MM-DD string for the cutoff date
    """
    since = since.strip()

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
        f"Invalid 'since' format: {since}. "
        "Use ISO duration (P3D, PT1H, P1W) or date (2026-01-15)"
    )


def _filter_by_date(items: list, since: str) -> list:
    """Filter items to only those updated since the given date/duration."""
    cutoff = _parse_since(since)
    return [
        item for item in items
        if item.tags.get("_updated_date", "0000-00-00") >= cutoff
    ]


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


def _record_to_item(rec, score: float = None) -> "Item":
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
    return Item(id=rec.id, summary=rec.summary, tags=tags, score=score)


import os
import subprocess
import sys

from .config import load_or_create_config, save_config, StoreConfig, EmbeddingIdentity
from .paths import get_config_dir, get_default_store_path
from .pending_summaries import PendingSummaryQueue
from .providers import get_registry
from .providers.base import (
    DocumentProvider,
    EmbeddingProvider,
    SummarizationProvider,
)
from .providers.embedding_cache import CachingEmbeddingProvider
from .document_store import VersionInfo
from .store import ChromaStore
from .types import Item, filter_non_system_tags, SYSTEM_TAG_PREFIX, parse_utc_timestamp


# Default max length for truncated placeholder summaries
TRUNCATE_LENGTH = 500

# Maximum attempts before giving up on a pending summary
MAX_SUMMARY_ATTEMPTS = 5


# Collection name validation: lowercase ASCII and underscores only

# Environment variable prefix for auto-applied tags
ENV_TAG_PREFIX = "KEEP_TAG_"

# Fixed ID for the current working context (singleton)
NOWDOC_ID = "now"


def _get_system_doc_dir() -> Path:
    """
    Get path to system docs, works in both dev and installed environments.

    Tries in order:
    1. Package data via importlib.resources (installed packages)
    2. Relative path inside package (development)
    3. Legacy path outside package (backwards compatibility)
    """
    # Try package data first (works for installed packages)
    try:
        with importlib.resources.as_file(
            importlib.resources.files("keep.data.system")
        ) as path:
            if path.exists():
                return path
    except (ModuleNotFoundError, TypeError):
        pass

    # Fallback to relative path inside package (development)
    dev_path = Path(__file__).parent / "data" / "system"
    if dev_path.exists():
        return dev_path

    # Legacy fallback (old structure)
    return Path(__file__).parent.parent / "docs" / "system"


# Path to system documents
SYSTEM_DOC_DIR = _get_system_doc_dir()

# Stable IDs for system documents (path-independent)
# Convention: filename sans .md, hyphens → /, prefixed with .
SYSTEM_DOC_IDS = {
    "now.md": ".now",
    "conversations.md": ".conversations",
    "domains.md": ".domains",
    "library.md": ".library",
    "tag-act.md": ".tag/act",
    "tag-status.md": ".tag/status",
    "tag-project.md": ".tag/project",
    "tag-topic.md": ".tag/topic",
    "tag-type.md": ".tag/type",
    "meta-todo.md": ".meta/todo",
    "meta-learnings.md": ".meta/learnings",
}

# Pattern for meta-doc query lines: key=value pairs separated by spaces
_META_QUERY_PAIR = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*=\S+$')
# Pattern for context-match lines: key= (bare, no value)
_META_CONTEXT_KEY = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)=$')


def _parse_meta_doc(content: str) -> tuple[list[dict[str, str]], list[str]]:
    """
    Parse meta-doc content into query lines and context-match keys.

    Returns:
        (query_lines, context_keys) where:
        - query_lines: list of dicts, each {key: value, ...} for AND queries
        - context_keys: list of tag keys for context matching
    """
    query_lines: list[dict[str, str]] = []
    context_keys: list[str] = []

    for line in content.split("\n"):
        line = line.strip()
        if not line:
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

    return query_lines, context_keys

# Old IDs for migration (maps old → new)
_OLD_ID_RENAMES = {
    "_system:now": ".now",
    "_system:conversations": ".conversations",
    "_system:domains": ".domains",
    "_system:library": ".library",
    "_tag:act": ".tag/act",
    "_tag:status": ".tag/status",
    "_tag:project": ".tag/project",
    "_tag:topic": ".tag/topic",
    "_now:default": "now",
}


def _load_frontmatter(path: Path) -> tuple[str, dict[str, str]]:
    """
    Load content and tags from a file with optional YAML frontmatter.

    Args:
        path: Path to the file

    Returns:
        (content, tags) tuple. Tags empty if no frontmatter.

    Raises:
        FileNotFoundError: If the file doesn't exist
    """
    text = path.read_text()

    # Parse YAML frontmatter if present
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            import yaml
            frontmatter = yaml.safe_load(parts[1])
            content = parts[2].lstrip("\n")
            if frontmatter:
                tags = frontmatter.get("tags", {})
                # Ensure all tag values are strings
                tags = {k: str(v) for k, v in tags.items()}
                return content, tags
            return content, {}

    return text, {}


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


class Keeper:
    """
    Reflective memory keeper - persistent storage with similarity search.

    Example:
        kp = Keeper()
        kp.update("file:///path/to/readme.md")
        results = kp.find("installation instructions")
    """
    
    def __init__(
        self,
        store_path: Optional[str | Path] = None,
        decay_half_life_days: float = 30.0
    ) -> None:
        """
        Initialize or open an existing reflective memory store.

        Args:
            store_path: Path to store directory. Uses default if not specified.
                       Overrides any store.path setting in config.
            decay_half_life_days: Memory decay half-life in days (ACT-R model).
                After this many days, an item's effective relevance is halved.
                Set to 0 or negative to disable decay.
        """
        self._decay_half_life_days = decay_half_life_days

        # Resolve config and store paths
        # If store_path is explicitly provided, use it as both config and store location
        # Otherwise, discover config via tree-walk and let config determine store
        if store_path is not None:
            self._store_path = Path(store_path).resolve()
            config_dir = self._store_path
        else:
            # Discover config directory (tree-walk or envvar)
            config_dir = get_config_dir()

        # Load or create configuration
        self._config: StoreConfig = load_or_create_config(config_dir)

        # If store_path wasn't explicit, resolve from config
        if store_path is None:
            self._store_path = get_default_store_path(self._config)

        # Initialize document provider (needed for most operations)
        registry = get_registry()
        self._document_provider: DocumentProvider = registry.create_document(
            self._config.document.name,
            self._config.document.params,
        )

        # Lazy-loaded providers (created on first use to avoid network access for read-only ops)
        self._embedding_provider: Optional[EmbeddingProvider] = None
        self._summarization_provider: Optional[SummarizationProvider] = None

        # Initialize pending summary queue
        queue_path = self._store_path / "pending_summaries.db"
        self._pending_queue = PendingSummaryQueue(queue_path)

        # Initialize document store (canonical records)
        from .document_store import DocumentStore
        doc_store_path = self._store_path / "documents.db"
        self._document_store = DocumentStore(doc_store_path)

        # Initialize ChromaDB store (embedding index)
        # Use dimension from stored identity if available (allows offline read-only access)
        embedding_dim = None
        if self._config.embedding_identity:
            embedding_dim = self._config.embedding_identity.dimension
        self._store = ChromaStore(
            self._store_path,
            embedding_dimension=embedding_dim,
        )

        # Check store consistency and reconcile in background if needed
        if self._check_store_consistency() and self._config.embedding is not None:
            import threading
            chroma_coll = self._resolve_chroma_collection()
            doc_coll = self._resolve_doc_collection()
            threading.Thread(
                target=self._auto_reconcile_safe, args=(chroma_coll, doc_coll), daemon=True
            ).start()

        # System doc migration deferred to first write (needs embeddings)
        from .config import SYSTEM_DOCS_VERSION
        self._needs_sysdoc_migration = (
            self._config.system_docs_version < SYSTEM_DOCS_VERSION
        )

    def _check_store_consistency(self) -> bool:
        """Check if DocumentStore and ChromaDB ID sets match.

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
            chroma_ids = self._store.list_ids(chroma_coll)
            doc_id_set = set(doc_ids)
            orphaned = [cid for cid in chroma_ids if cid not in doc_id_set]
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
        """Background-safe wrapper for auto-reconcile. Silently handles failures."""
        try:
            self._auto_reconcile(chroma_coll, doc_coll)
        except Exception as e:
            logger.debug("Background reconcile failed: %s", e)

    def _auto_reconcile(self, chroma_coll: str, doc_coll: str) -> None:
        """Fix store divergence using summaries (no content re-fetch needed).

        Requires an embedding provider to re-embed missing items.
        Skips entirely if no provider is configured or provider creation
        fails (avoids removing orphans without being able to re-embed).
        """
        if self._config.embedding is None:
            logger.debug("Skipping reconciliation: no embedding provider configured")
            return

        # Validate provider before entering loop — catches broken installs
        # (e.g. mlx configured but not installed) early and cleanly
        try:
            provider = self._get_embedding_provider()
        except Exception as e:
            logger.debug("Skipping reconciliation: provider unavailable: %s", e)
            return

        doc_ids = self._document_store.list_ids(doc_coll)
        missing = self._store.find_missing_ids(chroma_coll, doc_ids)

        # Items in DocumentStore but missing from ChromaDB — re-embed summary
        for doc_id in missing:
            try:
                record = self._document_store.get(doc_coll, doc_id)
                if record:
                    embedding = provider.embed(record.summary)
                    self._store.upsert(
                        collection=chroma_coll, id=doc_id,
                        embedding=embedding, summary=record.summary, tags=record.tags,
                    )
                    logger.info("Reconciled missing item: %s", doc_id)
            except Exception as e:
                logger.warning("Failed to reconcile %s: %s", doc_id, e)

        # Items in ChromaDB but not in DocumentStore — remove orphaned embeddings
        chroma_ids = self._store.list_ids(chroma_coll)
        doc_id_set = set(doc_ids)
        for orphan_id in chroma_ids:
            if orphan_id not in doc_id_set:
                try:
                    self._store.delete(chroma_coll, orphan_id)
                    logger.info("Removed orphaned embedding: %s", orphan_id)
                except Exception as e:
                    logger.warning("Failed to remove orphan %s: %s", orphan_id, e)

    def _migrate_system_documents(self) -> dict:
        """
        Migrate system documents to stable IDs and current version.

        Handles:
        - Migration from old file:// URIs to stable IDs
        - Rename of old prefixes (_system:, _tag:, _now:, _text:) to new (.x, .tag/x, now, %x)
        - Fresh creation for new stores
        - Version upgrades when bundled content changes

        Called during init. Only loads docs that don't already exist,
        so user modifications are preserved. Updates config version
        after successful migration.

        Returns:
            Dict with migration stats: created, migrated, skipped, cleaned
        """
        from .config import SYSTEM_DOCS_VERSION, save_config

        stats = {"created": 0, "migrated": 0, "skipped": 0, "cleaned": 0}

        # Skip if already at current version
        if self._config.system_docs_version >= SYSTEM_DOCS_VERSION:
            return stats

        # Build reverse lookup: filename -> new stable ID
        filename_to_id = {name: doc_id for name, doc_id in SYSTEM_DOC_IDS.items()}

        # First pass: clean up old file:// URIs with category=system tag
        try:
            old_system_docs = self.query_tag("category", "system")
            for doc in old_system_docs:
                if doc.id.startswith("file://") and doc.id.endswith(".md"):
                    filename = Path(doc.id.replace("file://", "")).name
                    new_id = filename_to_id.get(filename)
                    if new_id and not self.exists(new_id):
                        self.remember(doc.summary, id=new_id, tags=doc.tags)
                        self.delete(doc.id)
                        stats["migrated"] += 1
                        logger.info("Migrated system doc: %s -> %s", doc.id, new_id)
                    elif new_id:
                        self.delete(doc.id)
                        stats["cleaned"] += 1
                        logger.info("Cleaned up old system doc: %s", doc.id)
        except (OSError, ValueError, KeyError, RuntimeError) as e:
            logger.debug("Error scanning old system docs: %s", e)

        # Second pass: rename old prefixes to new
        # _system:foo → .foo, _tag:foo → .tag/foo, _now:default → now
        for old_id, new_id in _OLD_ID_RENAMES.items():
            try:
                old_item = self.get(old_id)
                if old_item and not self.exists(new_id):
                    self.remember(old_item.summary, id=new_id, tags=old_item.tags)
                    self.delete(old_id)
                    stats["migrated"] += 1
                    logger.info("Renamed ID: %s -> %s", old_id, new_id)
                elif old_item:
                    self.delete(old_id)
                    stats["cleaned"] += 1
            except (OSError, ValueError, KeyError, RuntimeError) as e:
                logger.debug("Error renaming %s: %s", old_id, e)

        # Rename _text:hash → %hash (transfer embeddings directly, no re-embedding)
        # Preserves original timestamps — these are user memories with meaningful dates
        try:
            doc_coll = self._resolve_doc_collection()
            chroma_coll_name = self._resolve_chroma_collection()
            old_text_docs = self._document_store.query_by_id_prefix(doc_coll, "_text:")
            for rec in old_text_docs:
                new_id = "%" + rec.id[len("_text:"):]
                if not self._document_store.get(doc_coll, new_id):
                    # Direct SQL copy preserving created_at and updated_at
                    tags_json = json.dumps(rec.tags, ensure_ascii=False)
                    self._document_store._conn.execute("""
                        INSERT OR REPLACE INTO documents
                        (id, collection, summary, tags_json, created_at, updated_at, content_hash, accessed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (new_id, doc_coll, rec.summary, tags_json,
                          rec.created_at, rec.updated_at, rec.content_hash, rec.accessed_at))
                    self._document_store._conn.commit()
                    # Transfer embedding from ChromaDB (no re-embedding needed)
                    try:
                        chroma_coll_obj = self._store._get_collection(chroma_coll_name)
                        result = chroma_coll_obj.get(
                            ids=[rec.id],
                            include=["embeddings", "metadatas", "documents"])
                        if result["ids"] and result["embeddings"]:
                            meta = result["metadatas"][0] or {}
                            chroma_coll_obj.upsert(
                                ids=[new_id],
                                embeddings=[result["embeddings"][0]],
                                documents=[result["documents"][0] or rec.summary],
                                metadatas=[meta])
                    except (ValueError, KeyError) as e:
                        logger.debug("ChromaDB transfer skipped for %s: %s", rec.id, e)
                self.delete(rec.id)
                stats["migrated"] += 1
                logger.info("Renamed text ID: %s -> %s", rec.id, new_id)
        except (OSError, ValueError, KeyError) as e:
            logger.debug("Error migrating _text: IDs: %s", e)

        # Third pass: remove system docs no longer bundled
        _RETIRED_SYSTEM_IDS = [".meta/decisions"]
        for old_id in _RETIRED_SYSTEM_IDS:
            try:
                if self.exists(old_id):
                    self.delete(old_id)
                    stats["cleaned"] += 1
                    logger.info("Removed retired system doc: %s", old_id)
            except (OSError, ValueError, KeyError) as e:
                logger.debug("Error removing retired doc %s: %s", old_id, e)

        # Fourth pass: create or update system docs from bundled content
        for path in SYSTEM_DOC_DIR.glob("*.md"):
            new_id = SYSTEM_DOC_IDS.get(path.name)
            if new_id is None:
                logger.debug("Skipping unknown system doc: %s", path.name)
                continue

            try:
                content, tags = _load_frontmatter(path)
                bundled_hash = _content_hash(content)
                tags["category"] = "system"
                tags["bundled_hash"] = bundled_hash

                # Check for user edits before overwriting
                existing_doc = self._document_store.get(doc_coll, new_id)
                if existing_doc:
                    prev_hash = existing_doc.tags.get("bundled_hash")
                    if prev_hash and existing_doc.content_hash != prev_hash:
                        # User edited this doc — preserve their version
                        stats["skipped"] += 1
                        logger.info("Preserving user-edited system doc: %s", new_id)
                        continue

                # Store to DocumentStore directly (always works, no embedding needed).
                # System docs are reference material — store full verbatim content.
                from .types import utc_now as _utc_now
                now_ts = _utc_now()
                tags.setdefault("_created", now_ts)
                tags["_updated"] = now_ts
                tags["_updated_date"] = now_ts[:10]
                tags["_source"] = "inline"
                self._document_store.upsert(
                    collection=doc_coll, id=new_id, summary=content,
                    tags=tags, content_hash=bundled_hash,
                )
                # Also embed to ChromaDB if provider is available
                try:
                    embedding = self._get_embedding_provider().embed(content)
                    self._store.upsert(
                        collection=chroma_coll_name, id=new_id,
                        embedding=embedding, summary=content, tags=tags,
                    )
                except (RuntimeError, Exception) as e:
                    # No embedding provider or embedding failed — that's fine,
                    # reconciliation will pick it up later
                    logger.debug("Skipped embedding for system doc %s: %s", new_id, e)
                if existing_doc:
                    stats["migrated"] += 1
                    logger.info("Updated system doc: %s", new_id)
                else:
                    stats["created"] += 1
                    logger.info("Created system doc: %s", new_id)
            except FileNotFoundError:
                # System file missing - skip silently
                pass

        # Update config version
        self._config.system_docs_version = SYSTEM_DOCS_VERSION
        save_config(self._config)

        return stats

    def _get_embedding_provider(self) -> EmbeddingProvider:
        """
        Get embedding provider, creating it lazily on first use.

        This allows read-only operations to work offline without loading
        the embedding model (which may try to reach HuggingFace).

        For MLX (local GPU) providers, wraps with a lifecycle lock that
        serializes model access across processes to prevent GPU memory
        exhaustion.
        """
        if self._embedding_provider is None:
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
            # Validate or record embedding identity
            self._validate_embedding_identity(self._embedding_provider)
            # Update store's embedding dimension if it wasn't known at init
            if self._store._embedding_dimension is None:
                self._store._embedding_dimension = self._embedding_provider.dimension
        return self._embedding_provider

    def _get_summarization_provider(self) -> SummarizationProvider:
        """
        Get summarization provider, creating it lazily on first use.

        For MLX (local GPU) providers, wraps with a lifecycle lock.
        """
        if self._summarization_provider is None:
            registry = get_registry()
            provider = registry.create_summarization(
                self._config.summarization.name,
                self._config.summarization.params,
            )
            if self._config.summarization.name == "mlx":
                from .model_lock import LockedSummarizationProvider
                provider = LockedSummarizationProvider(
                    provider,
                    self._store_path / ".summarization.lock",
                )
            self._summarization_provider = provider
        return self._summarization_provider

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
            similar = self.find_similar(id, limit=20)
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
        and triggers background reindex into the new ChromaDB collection.
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
                # Trigger background reindex into new ChromaDB collection
                self._trigger_background_reindex()

    def _trigger_background_reindex(self) -> None:
        """Spawn background thread to populate new ChromaDB collection from DocumentStore."""
        import threading
        chroma_coll = self._resolve_chroma_collection()
        doc_coll = self._resolve_doc_collection()
        threading.Thread(
            target=self._background_reindex_safe,
            args=(chroma_coll, doc_coll),
            daemon=True,
        ).start()

    def _background_reindex_safe(self, chroma_coll: str, doc_coll: str) -> None:
        """Background-safe wrapper for reindex. Silently handles failures."""
        try:
            self._background_reindex(chroma_coll, doc_coll)
        except Exception as e:
            logger.warning("Background reindex failed: %s", e)

    def _background_reindex(self, chroma_coll: str, doc_coll: str) -> None:
        """Populate ChromaDB collection from DocumentStore summaries using batch embedding."""
        doc_ids = self._document_store.list_ids(doc_coll)
        if not doc_ids:
            return

        # Find which IDs are missing from ChromaDB
        missing = self._store.find_missing_ids(chroma_coll, doc_ids)
        if not missing:
            logger.info("Background reindex: all %d items already indexed", len(doc_ids))
            return

        logger.info("Background reindex: %d of %d items to index", len(missing), len(doc_ids))

        # Collect records to embed
        records = []
        for doc_id in missing:
            record = self._document_store.get(doc_coll, doc_id)
            if record:
                records.append((doc_id, record))

        # Batch embed and upsert
        BATCH_SIZE = 50
        indexed = 0
        provider = self._get_embedding_provider()

        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            try:
                summaries = [rec.summary for _, rec in batch]
                embeddings = provider.embed_batch(summaries)
                for (doc_id, rec), embedding in zip(batch, embeddings):
                    self._store.upsert(
                        collection=chroma_coll, id=doc_id,
                        embedding=embedding, summary=rec.summary, tags=rec.tags,
                    )
                    indexed += 1
            except Exception:
                # Fall back to individual embedding on batch failure
                for doc_id, rec in batch:
                    try:
                        embedding = provider.embed(rec.summary)
                        self._store.upsert(
                            collection=chroma_coll, id=doc_id,
                            embedding=embedding, summary=rec.summary, tags=rec.tags,
                        )
                        indexed += 1
                    except Exception as e:
                        logger.warning("Reindex failed for %s: %s", doc_id, e)

        # Also handle versioned embeddings (@v{N})
        for doc_id, record in records:
            for vi in self._document_store.list_versions(doc_coll, doc_id, limit=100):
                try:
                    emb = provider.embed(vi.summary)
                    versioned_id = f"{doc_id}@v{vi.version}"
                    self._store.upsert_version(
                        collection=chroma_coll, id=doc_id,
                        version=vi.version, embedding=emb,
                        summary=vi.summary, tags=vi.tags,
                    )
                except Exception:
                    pass  # Version embedding failure is non-critical

        logger.info("Background reindex complete: %d items indexed", indexed)

    def reindex(self) -> dict:
        """
        Rebuild search index with current embedding provider (foreground).

        Re-embeds all items from DocumentStore into the current ChromaDB
        collection. Use as an explicit backstop when background reindex
        didn't complete.

        Returns:
            Dict with stats: total, indexed, failed
        """
        chroma_coll = self._resolve_chroma_collection()
        doc_coll = self._resolve_doc_collection()

        doc_ids = self._document_store.list_ids(doc_coll)
        total = len(doc_ids)
        indexed = 0
        failed = 0

        BATCH_SIZE = 50
        provider = self._get_embedding_provider()

        # Collect all records
        records = []
        for doc_id in doc_ids:
            record = self._document_store.get(doc_coll, doc_id)
            if record:
                records.append((doc_id, record))

        # Batch embed and upsert
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            try:
                summaries = [rec.summary for _, rec in batch]
                embeddings = provider.embed_batch(summaries)
                for (doc_id, rec), embedding in zip(batch, embeddings):
                    self._store.upsert(
                        collection=chroma_coll, id=doc_id,
                        embedding=embedding, summary=rec.summary, tags=rec.tags,
                    )
                    indexed += 1
            except Exception:
                for doc_id, rec in batch:
                    try:
                        embedding = provider.embed(rec.summary)
                        self._store.upsert(
                            collection=chroma_coll, id=doc_id,
                            embedding=embedding, summary=rec.summary, tags=rec.tags,
                        )
                        indexed += 1
                    except Exception as e:
                        logger.warning("Reindex failed for %s: %s", doc_id, e)
                        failed += 1

        # Also re-embed versions
        version_count = 0
        for doc_id, record in records:
            for vi in self._document_store.list_versions(doc_coll, doc_id, limit=100):
                try:
                    emb = provider.embed(vi.summary)
                    self._store.upsert_version(
                        collection=chroma_coll, id=doc_id,
                        version=vi.version, embedding=emb,
                        summary=vi.summary, tags=vi.tags,
                    )
                    version_count += 1
                except Exception:
                    pass

        return {"total": total, "indexed": indexed, "failed": failed, "versions": version_count}

    @property
    def embedding_identity(self) -> EmbeddingIdentity | None:
        """Current embedding identity (provider, model, dimension)."""
        return self._config.embedding_identity
    
    def _resolve_chroma_collection(self) -> str:
        """ChromaDB collection name derived from embedding identity."""
        if self._config.embedding_identity:
            return self._config.embedding_identity.key
        return "default"

    def _resolve_doc_collection(self) -> str:
        """DocumentStore collection — always 'default'."""
        return "default"
    
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
    ) -> Item:
        """Core upsert logic shared by update() and remember()."""
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
            merged_tags.update(self._config.default_tags)

        env_tags = _get_env_tags()
        merged_tags.update(env_tags)

        if tags:
            merged_tags.update(filter_non_system_tags(tags))

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
            return _record_to_item(existing_doc)

        # Get embedding: reuse stored if content unchanged, compute if new/changed
        if content_unchanged:
            embedding = self._store.get_embedding(chroma_coll, id)
            if embedding is None:
                embedding = self._get_embedding_provider().embed(content)
        else:
            embedding = self._get_embedding_provider().embed(content)

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
        )

        self._store.upsert(
            collection=chroma_coll,
            id=id,
            embedding=embedding,
            summary=final_summary,
            tags=merged_tags,
        )

        # If content changed and we archived a version, also store versioned embedding
        if existing_doc is not None and content_changed:
            version_count = self._document_store.version_count(doc_coll, id)
            if version_count > 0:
                if old_embedding is None:
                    old_embedding = self._get_embedding_provider().embed(existing_doc.summary)
                self._store.upsert_version(
                    collection=chroma_coll,
                    id=id,
                    version=version_count,
                    embedding=old_embedding,
                    summary=existing_doc.summary,
                    tags=existing_doc.tags,
                )

        # Spawn background processor if needed
        if summary is None and len(content) > max_len and (not content_unchanged or tags_changed):
            self._spawn_processor()

        return _record_to_item(result)

    def update(
        self,
        id: str,
        tags: Optional[dict[str, str]] = None,
        *,
        summary: Optional[str] = None,
        source_tags: Optional[dict[str, str]] = None,  # Deprecated alias
    ) -> Item:
        """
        Insert or update a document in the store.

        Fetches the document, generates embeddings and summary, then stores it.

        **Summary behavior:**
        - If summary is provided, use it (skips auto-summarization)
        - For large content, summarization is async (truncated placeholder
          stored immediately, real summary generated in background)

        **Update behavior:**
        - Summary: Replaced with user-provided or newly generated summary
        - Tags: Merged - existing tags are preserved, new tags override
          on key collision. System tags (prefixed with _) are always managed by
          the system.

        Args:
            id: URI of document to fetch and index
            tags: User-provided tags to merge with existing tags
            summary: User-provided summary (skips auto-summarization if given)
            source_tags: Deprecated alias for 'tags'

        Returns:
            The stored Item with merged tags and new summary
        """
        if source_tags is not None:
            import warnings
            warnings.warn(
                "source_tags is deprecated, use 'tags' instead",
                DeprecationWarning,
                stacklevel=2
            )
            if tags is None:
                tags = source_tags

        doc = self._document_provider.fetch(id)

        system_tags = {"_source": "uri"}
        if doc.content_type:
            system_tags["_content_type"] = doc.content_type

        return self._upsert(
            id, doc.content,
            tags=tags, summary=summary,
            system_tags=system_tags,
        )

    def remember(
        self,
        content: str,
        *,
        id: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[dict[str, str]] = None,
        source_tags: Optional[dict[str, str]] = None,  # Deprecated alias
    ) -> Item:
        """
        Store inline content directly (without fetching from a URI).

        Use for conversation snippets, notes, insights.

        **Smart summary behavior:**
        - If summary is provided, use it (skips auto-summarization)
        - If content is short (≤ max_summary_length), use content verbatim
        - For large content, summarization is async (truncated placeholder
          stored immediately, real summary generated in background)

        **Update behavior (when id already exists):**
        - Summary: Replaced with user-provided, content, or generated summary
        - Tags: Merged - existing tags preserved, new tags override
          on key collision. System tags (prefixed with _) are always managed by
          the system.

        Args:
            content: Text to store and index
            id: Optional custom ID (auto-generated if None)
            summary: User-provided summary (skips auto-summarization if given)
            tags: User-provided tags to merge with existing tags
            source_tags: Deprecated alias for 'tags'

        Returns:
            The stored Item with merged tags and new summary
        """
        if source_tags is not None:
            import warnings
            warnings.warn(
                "source_tags is deprecated, use 'tags' instead",
                DeprecationWarning,
                stacklevel=2
            )
            if tags is None:
                tags = source_tags

        if id is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
            id = f"mem:{timestamp}"

        return self._upsert(
            id, content,
            tags=tags, summary=summary,
            system_tags={"_source": "inline"},
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
        query: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
    ) -> list[Item]:
        """
        Find items using semantic similarity search.

        Scores are adjusted by recency decay (ACT-R model) - older items
        have reduced effective relevance unless recently accessed.

        Args:
            query: Search query text
            limit: Maximum results to return
            since: Only include items updated since (ISO duration like P3D, or date)
        """
        chroma_coll = self._resolve_chroma_collection()
        doc_coll = self._resolve_doc_collection()

        # Embed query
        embedding = self._get_embedding_provider().embed(query)

        # Search (fetch extra to account for re-ranking and date filtering)
        fetch_limit = limit * 2 if self._decay_half_life_days > 0 else limit
        if since is not None:
            fetch_limit = max(fetch_limit, limit * 3)  # Fetch more when filtering
        results = self._store.query_embedding(chroma_coll, embedding, limit=fetch_limit)

        # Convert to Items and apply decay
        items = [r.to_item() for r in results]
        items = self._apply_recency_decay(items)

        # Apply date filter if specified
        if since is not None:
            items = _filter_by_date(items, since)

        final = items[:limit]
        # Touch accessed_at for returned items
        if final:
            self._document_store.touch_many(doc_coll, [i.id for i in final])
        return final

    def find_similar(
        self,
        id: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
        include_self: bool = False,
    ) -> list[Item]:
        """
        Find items similar to an existing item.

        Args:
            id: ID of item to find similar items for
            limit: Maximum results to return
            since: Only include items updated since (ISO duration like P3D, or date)
            include_self: Include the queried item in results
        """
        chroma_coll = self._resolve_chroma_collection()
        doc_coll = self._resolve_doc_collection()

        # Get the item's stored embedding from ChromaDB
        item = self._store.get(chroma_coll, id)
        if item is None:
            raise KeyError(f"Item not found: {id}")

        embedding = self._store.get_embedding(chroma_coll, id)
        if embedding is None:
            embedding = self._get_embedding_provider().embed(item.summary)
        actual_limit = limit + 1 if not include_self else limit
        if since is not None:
            actual_limit = max(actual_limit, limit * 3)
        results = self._store.query_embedding(chroma_coll, embedding, limit=actual_limit)

        # Filter self if needed
        if not include_self:
            results = [r for r in results if r.id != id]

        # Convert to Items and apply decay
        items = [r.to_item() for r in results]
        items = self._apply_recency_decay(items)

        # Apply date filter if specified
        if since is not None:
            items = _filter_by_date(items, since)

        final = items[:limit]
        # Touch accessed_at for returned items
        if final:
            self._document_store.touch_many(doc_coll, [i.id for i in final])
        return final

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

        # Fetch more than needed to account for version filtering
        fetch_limit = limit * 3
        results = self._store.query_embedding(chroma_coll, embedding, limit=fetch_limit)

        # Convert to Items
        items = [r.to_item() for r in results]

        # Extract base ID of source document
        source_base_id = id.split("@v")[0] if "@v" in id else id

        # Filter to distinct base IDs, excluding source document
        seen_base_ids: set[str] = set()
        filtered: list[Item] = []
        for item in items:
            # Get base ID from tags or parse from ID
            base_id = item.tags.get("_base_id", item.id.split("@v")[0] if "@v" in item.id else item.id)

            # Skip versions of source document
            if base_id == source_base_id:
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
        version_count = self._document_store.version_count(doc_coll, base_id)
        return version_count - int(version_tag) + 1

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

            query_lines, context_keys = _parse_meta_doc(rec.summary)
            if not query_lines:
                continue

            # Get context values from current item's tags
            context_values: dict[str, str] = {}
            for key in context_keys:
                val = current_tags.get(key)
                if val and not key.startswith("_"):
                    context_values[key] = val

            # Build expanded queries: cross-product of query lines × context values
            expanded: list[dict[str, str]] = []
            if context_values:
                for query in query_lines:
                    for ctx_key, ctx_val in context_values.items():
                        expanded.append({**query, ctx_key: ctx_val})
            else:
                # No context → use query lines as-is
                expanded = list(query_lines)

            # Run each expanded query, union results (fetch generously for ranking)
            seen_ids: set[str] = set()
            matches: list[Item] = []
            for query in expanded:
                try:
                    items = self.query_tag(
                        limit=100,  # fetch all candidates for ranking
                        **query,
                    )
                except (ValueError, Exception):
                    continue
                for item in items:
                    # Skip the current item, meta-docs, and dupes
                    if item.id == item_id or item.id.startswith(".meta/") or item.id in seen_ids:
                        continue
                    seen_ids.add(item.id)
                    matches.append(item)

            if not matches:
                continue

            # Rank by similarity to current item + recency decay
            matches = self._rank_by_relevance(self._resolve_chroma_collection(), item_id, matches)
            result[short_name] = matches[:limit_per_doc]

        return result

    def _rank_by_relevance(
        self,
        coll: str,
        anchor_id: str,
        candidates: list[Item],
    ) -> list[Item]:
        """
        Rank candidate items by similarity to anchor + recency decay.

        Uses stored embeddings from ChromaDB — no re-embedding needed.
        Falls back to recency-only ranking if embeddings unavailable.
        """
        import math

        if not candidates:
            return candidates

        # Get anchor embedding from ChromaDB
        try:
            chroma_coll = self._store._get_collection(coll)
            anchor_result = chroma_coll.get(
                ids=[anchor_id], include=["embeddings"]
            )
            if not anchor_result["ids"] or not anchor_result["embeddings"]:
                return self._apply_recency_decay(candidates)
            anchor_emb = anchor_result["embeddings"][0]

            # Batch-fetch candidate embeddings
            candidate_ids = [c.id for c in candidates]
            cand_result = chroma_coll.get(
                ids=candidate_ids, include=["embeddings"]
            )
        except Exception as e:
            logger.debug("Embedding lookup failed, falling back to recency: %s", e)
            return self._apply_recency_decay(candidates)

        # Build id → embedding lookup (some candidates may not have embeddings)
        emb_lookup: dict[str, list[float]] = {}
        if cand_result["ids"] and cand_result["embeddings"]:
            for cid, cemb in zip(cand_result["ids"], cand_result["embeddings"]):
                if cemb is not None:
                    emb_lookup[cid] = cemb

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

    def query_fulltext(
        self,
        query: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
    ) -> list[Item]:
        """
        Search item summaries using full-text search.

        Args:
            query: Text to search for in summaries
            limit: Maximum results to return
            since: Only include items updated since (ISO duration like P3D, or date)
        """
        chroma_coll = self._resolve_chroma_collection()
        doc_coll = self._resolve_doc_collection()

        # Fetch extra when filtering by date
        fetch_limit = limit * 3 if since is not None else limit
        results = self._store.query_fulltext(chroma_coll, query, limit=fetch_limit)
        items = [r.to_item() for r in results]

        # Apply date filter if specified
        if since is not None:
            items = _filter_by_date(items, since)

        final = items[:limit]
        # Touch accessed_at for returned items
        if final:
            self._document_store.touch_many(doc_coll, [i.id for i in final])
        return final

    def query_tag(
        self,
        key: Optional[str] = None,
        value: Optional[str] = None,
        *,
        limit: int = 100,
        since: Optional[str] = None,
        **tags: str
    ) -> list[Item]:
        """
        Find items by tag(s).

        Usage:
            # Key only: find all docs with this tag key (any value)
            query_tag("project")

            # Key with value: find docs with specific tag value
            query_tag("project", "myapp")

            # Multiple tags via kwargs
            query_tag(tradition="buddhist", source="mn22")

        Args:
            key: Tag key to search for
            value: Tag value (optional, any value if not provided)
            limit: Maximum results to return
            since: Only include items updated since (ISO duration like P3D, or date)
            **tags: Additional tag filters as keyword arguments
        """
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Key-only query: find docs that have this tag key (any value)
        # Uses DocumentStore which supports efficient SQL date filtering
        if key is not None and value is None and not tags:
            # Convert since to cutoff date for SQL query
            since_date = _parse_since(since) if since else None
            docs = self._document_store.query_by_tag_key(
                doc_coll, key, limit=limit, since_date=since_date
            )
            return [_record_to_item(d) for d in docs]

        # Build tag filter from positional or keyword args
        tag_filter = {}

        if key is not None and value is not None:
            tag_filter[key] = value

        if tags:
            tag_filter.update(tags)

        if not tag_filter:
            raise ValueError("At least one tag must be specified")

        # Build where clause for tag filters only
        # (ChromaDB $gte doesn't support string dates, so date filtering is done post-query)
        where_conditions = [{k: v} for k, v in tag_filter.items()]

        # Use $and if multiple conditions, otherwise single condition
        if len(where_conditions) == 1:
            where = where_conditions[0]
        else:
            where = {"$and": where_conditions}

        # Fetch extra when filtering by date
        fetch_limit = limit * 3 if since is not None else limit
        results = self._store.query_metadata(chroma_coll, where, limit=fetch_limit)
        items = [r.to_item() for r in results]

        # Apply date filter if specified (post-filter)
        if since is not None:
            items = _filter_by_date(items, since)

        return items[:limit]

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

        Reads from document store (canonical), falls back to ChromaDB for legacy data.
        Touches accessed_at on successful retrieval.
        """
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Try document store first (canonical)
        doc_record = self._document_store.get(doc_coll, id)
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
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        version_count = self._document_store.version_count(doc_coll, id)

        if version_count == 0:
            # No history — full delete
            self.delete(id)
            return None

        # Get the versioned ChromaDB ID we need to promote
        versioned_chroma_id = f"{id}@v{version_count}"

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
                tags=restored.tags,
            )

        # Delete the versioned entry from ChromaDB
        chroma_coll_handle = self._store._get_collection(chroma_coll)
        try:
            chroma_coll_handle.delete(ids=[versioned_chroma_id])
        except ValueError:
            pass  # May not exist if it was a tag-only change

        return self.get(id)

    # -------------------------------------------------------------------------
    # Current Working Context (Now)
    # -------------------------------------------------------------------------

    def get_now(self) -> Item:
        """
        Get the current working intentions.

        A singleton document representing what you're currently working on.
        If it doesn't exist, creates one with default content and tags from
        the bundled system now.md file.

        Returns:
            The current intentions Item (never None - auto-creates if missing)
        """
        item = self.get(NOWDOC_ID)
        if item is None:
            # First-time initialization with default content and tags
            try:
                default_content, default_tags = _load_frontmatter(SYSTEM_DOC_DIR / "now.md")
            except FileNotFoundError:
                # Fallback if system file is missing
                default_content = "# Now\n\nYour working context."
                default_tags = {}
            item = self.set_now(default_content, tags=default_tags)
        return item

    def set_now(
        self,
        content: str,
        *,
        tags: Optional[dict[str, str]] = None,
    ) -> Item:
        """
        Set the current working intentions.

        Updates the singleton intentions with new content. Uses remember()
        internally with the fixed NOWDOC_ID.

        Args:
            content: New content for the current intentions
            tags: Optional additional tags to apply

        Returns:
            The updated context Item
        """
        return self.remember(content, id=NOWDOC_ID, tags=tags)

    def list_system_documents(self) -> list[Item]:
        """
        List all system documents.

        System documents are identified by the `category: system` tag.
        These are preloaded on init and provide foundational content.

        Returns:
            List of system document Items
        """
        return self.query_tag("category", "system")

    def reset_system_documents(self) -> dict:
        """
        Force reload all system documents from bundled content.

        This overwrites any user modifications to system documents.
        Use with caution - primarily for recovery or testing.

        Returns:
            Dict with stats: reset count
        """
        from .config import SYSTEM_DOCS_VERSION, save_config

        stats = {"reset": 0}
        doc_coll = self._resolve_doc_collection()

        for path in SYSTEM_DOC_DIR.glob("*.md"):
            new_id = SYSTEM_DOC_IDS.get(path.name)
            if new_id is None:
                continue

            try:
                content, tags = _load_frontmatter(path)
                bundled_hash = _content_hash(content)
                tags["category"] = "system"
                tags["bundled_hash"] = bundled_hash

                # Delete existing (if any) and create fresh
                self.delete(new_id)
                self.remember(content, id=new_id, tags=tags)
                self._document_store.upsert(
                    collection=doc_coll, id=new_id, summary=content,
                    tags=self._document_store.get(doc_coll, new_id).tags,
                    content_hash=bundled_hash,
                )
                stats["reset"] += 1
                logger.info("Reset system doc: %s", new_id)

            except FileNotFoundError:
                logger.warning("System doc file not found: %s", path)

        # Update config version
        self._config.system_docs_version = SYSTEM_DOCS_VERSION
        save_config(self._config)

        return stats

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

        # Dual-write to both stores
        self._document_store.update_tags(doc_coll, id, final_tags)
        self._store.update_tags(chroma_coll, id, final_tags)

        # Return updated item
        return self.get(id)

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

        Returns count from document store if available, else ChromaDB.
        """
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()
        doc_count = self._document_store.count(doc_coll)
        if doc_count > 0:
            return doc_count
        return self._store.count(chroma_coll)

    def list_recent(
        self,
        limit: int = 10,
        *,
        since: Optional[str] = None,
        order_by: str = "updated",
        include_history: bool = False,
    ) -> list[Item]:
        """
        List recent items ordered by timestamp.

        Args:
            limit: Maximum number to return (default 10)
            since: Only include items updated since (ISO duration like P3D, or date)
            order_by: Sort order - "updated" (default) or "accessed"
            include_history: Include archived versions alongside current items

        Returns:
            List of Items, most recent first
        """
        doc_coll = self._resolve_doc_collection()

        # Fetch extra when filtering by date
        fetch_limit = limit * 3 if since is not None else limit
        if include_history:
            records = self._document_store.list_recent_with_history(doc_coll, fetch_limit, order_by=order_by)
        else:
            records = self._document_store.list_recent(doc_coll, fetch_limit, order_by=order_by)
        items = [_record_to_item(rec) for rec in records]

        # Apply date filter if specified
        if since is not None:
            items = _filter_by_date(items, since)

        return items[:limit]

    def embedding_cache_stats(self) -> dict:
        """
        Get embedding cache statistics.

        Returns dict with: entries, hits, misses, hit_rate, cache_path
        Returns {"loaded": False} if embedding provider hasn't been loaded yet.
        """
        if self._embedding_provider is None:
            return {"loaded": False}
        if isinstance(self._embedding_provider, CachingEmbeddingProvider):
            return self._embedding_provider.stats()
        return {"enabled": False}

    # -------------------------------------------------------------------------
    # Pending Summaries
    # -------------------------------------------------------------------------

    def process_pending(self, limit: int = 10) -> dict:
        """
        Process pending summaries queued by lazy update/remember.

        Generates real summaries for items that were indexed with
        truncated placeholders. Updates the stored items in place.

        When items have user tags (non-system tags), context is gathered
        from similar items with matching tags to produce contextual summaries.

        Items that fail MAX_SUMMARY_ATTEMPTS times are removed from
        the queue (the truncated placeholder remains in the store).

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
                # Give up - remove from queue, keep truncated placeholder
                self._pending_queue.complete(item.id, item.collection)
                result["abandoned"] += 1
                logger.warning(
                    "Abandoned pending summary after %d attempts: %s",
                    item.attempts, item.id
                )
                continue

            try:
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

                # Remove from queue
                self._pending_queue.complete(item.id, item.collection)
                result["processed"] += 1

            except Exception as e:
                # Leave in queue for retry (attempt counter already incremented)
                result["failed"] += 1
                error_msg = f"{item.id}: {type(e).__name__}: {e}"
                result["errors"].append(error_msg)
                logger.warning("Failed to summarize %s (attempt %d): %s",
                             item.id, item.attempts, e)

        return result

    def pending_count(self) -> int:
        """Get count of pending summaries awaiting processing."""
        return self._pending_queue.count()

    def pending_stats(self) -> dict:
        """
        Get pending summary queue statistics.

        Returns dict with: pending, collections, max_attempts, oldest, queue_path
        """
        return self._pending_queue.stats()

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

        try:
            if self._is_processor_running():
                return False

            # Spawn detached process
            # Use sys.executable to ensure we use the same Python
            cmd = [
                sys.executable, "-m", "keep.cli",
                "process-pending",
                "--daemon",
                "--store", str(self._store_path),
            ]

            # Platform-specific detachment
            kwargs: dict = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }

            if sys.platform != "win32":
                # Unix: start new session to fully detach
                kwargs["start_new_session"] = True
            else:
                # Windows: use CREATE_NEW_PROCESS_GROUP
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

            subprocess.Popen(cmd, **kwargs)
            return True

        except Exception as e:
            # Spawn failed - log for debugging, queue will be processed later
            logger.warning("Failed to spawn background processor: %s", e)
            return False
        finally:
            spawn_lock.release()

    def reconcile(
        self,
        fix: bool = False,
    ) -> dict:
        """
        Check and optionally fix consistency between DocumentStore and ChromaDB.

        Detects:
        - Documents in DocumentStore missing from ChromaDB (not searchable)
        - Documents in ChromaDB missing from DocumentStore (orphaned embeddings)

        Args:
            fix: If True, re-index documents missing from ChromaDB

        Returns:
            Dict with 'missing_from_chroma', 'orphaned_in_chroma', 'fixed' counts
        """
        doc_coll = self._resolve_doc_collection()
        chroma_coll = self._resolve_chroma_collection()

        # Find mismatches between stores
        doc_ids = self._document_store.list_ids(doc_coll)
        missing_from_chroma = self._store.find_missing_ids(chroma_coll, doc_ids)

        chroma_ids = self._store.list_ids(chroma_coll)
        doc_id_set = set(doc_ids)
        orphaned_in_chroma = {cid for cid in chroma_ids if cid not in doc_id_set}

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
                            tags=doc_record.tags,
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
            "missing_from_chroma": len(missing_from_chroma),
            "orphaned_in_chroma": len(orphaned_in_chroma),
            "fixed": fixed,
            "removed": removed,
            "missing_ids": list(missing_from_chroma) if missing_from_chroma else [],
            "orphaned_ids": list(orphaned_in_chroma) if orphaned_in_chroma else [],
        }

    def close(self) -> None:
        """
        Close resources (stores, caches, queues).

        Releases model locks (freeing GPU memory) before releasing file locks,
        ensuring the next process gets a clean GPU.
        """
        # Release locked model providers (frees GPU memory + gc before flock release)
        if self._embedding_provider is not None:
            # The locked provider may be inside a CachingEmbeddingProvider
            inner = getattr(self._embedding_provider, '_provider', None)
            if hasattr(inner, 'release'):
                inner.release()

        if self._summarization_provider is not None:
            if hasattr(self._summarization_provider, 'release'):
                self._summarization_provider.release()

        # Close ChromaDB store
        if hasattr(self, '_store') and self._store is not None:
            self._store.close()

        # Close document store (SQLite)
        if hasattr(self, '_document_store') and self._document_store is not None:
            self._document_store.close()

        # Close embedding cache if it was loaded
        if self._embedding_provider is not None:
            if hasattr(self._embedding_provider, '_cache'):
                cache = self._embedding_provider._cache
                if hasattr(cache, 'close'):
                    cache.close()

        # Close pending summary queue
        if hasattr(self, '_pending_queue'):
            self._pending_queue.close()

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
