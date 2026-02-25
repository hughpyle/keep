"""
Data types for reflective memory.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse


# System tag prefix - tags starting with this are managed by the system
SYSTEM_TAG_PREFIX = "_"

# Tags used internally but hidden from display output
# These exist for efficient queries/sorting but aren't user-facing
INTERNAL_TAGS = frozenset({"_updated_date", "_accessed_date", "_focus_part"})


def utc_now() -> str:
    """Current UTC timestamp in canonical format: YYYY-MM-DDTHH:MM:SS.

    All timestamps in keep are UTC, stored without timezone suffix.
    This is the single source of truth for timestamp formatting.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_utc_timestamp(ts: str) -> datetime:
    """Parse a stored timestamp string to a timezone-aware UTC datetime.

    Handles both the canonical format (no suffix) and legacy formats
    that may include microseconds, 'Z', or '+00:00' suffixes.
    """
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_date(utc_iso: str) -> str:
    """Convert a UTC ISO timestamp to a local-timezone date string (YYYY-MM-DD).

    Used for short-form display dates. Returns empty string for empty/invalid input.
    """
    if not utc_iso:
        return ""
    try:
        dt = parse_utc_timestamp(utc_iso)
        return dt.astimezone().strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return utc_iso[:10] if len(utc_iso) >= 10 else utc_iso


# Tag keys must be simple: alphanumeric, underscore, hyphen (no JSON path chars)
_TAG_KEY_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_-]*$')

MAX_ID_LENGTH = 1024
MAX_TAG_KEY_LENGTH = 128
MAX_TAG_VALUE_LENGTH = 4096

# IDs: printable characters minus control chars and a small blocklist.
# Blocked: null bytes (\x00), control chars (\x01-\x1f), DEL (\x7f),
#   backslash (path confusion), backtick (shell), angle brackets (HTML/XML),
#   pipe (shell), semicolon (shell/SQL), double quote, single quote
_ID_BLOCKED_RE = re.compile(r'[\x00-\x1f\x7f\\`<>|;"\']')

# Part ID suffix: @p or @P followed by optional braces and digits
_PART_ID_RE = re.compile(r'@[pP]\{?\d+\}?$')


def is_part_id(id: str) -> bool:
    """Check if an ID looks like a part reference (e.g. 'doc@p3' or 'doc@P{3}')."""
    return bool(_PART_ID_RE.search(id))


def validate_tag_key(key: str) -> None:
    """Validate a tag key is safe for JSON path queries."""
    if not key or len(key) > MAX_TAG_KEY_LENGTH:
        raise ValueError(f"Tag key must be 1-{MAX_TAG_KEY_LENGTH} characters: {key!r}")
    if not _TAG_KEY_RE.match(key):
        msg = f"Tag key contains invalid characters (allowed: a-z, 0-9, _, -): {key!r}"
        if ":" in key:
            parts = key.split(":", 1)
            msg += f". Use separate key and value: tags={{'{parts[0]}': '{parts[1]}'}}"
        raise ValueError(msg)


def validate_id(id: str) -> None:
    """Validate a document ID — length and no dangerous characters."""
    if not id or len(id) > MAX_ID_LENGTH:
        raise ValueError(f"ID must be 1-{MAX_ID_LENGTH} characters")
    if _ID_BLOCKED_RE.search(id):
        raise ValueError(f"ID contains invalid characters: {id!r}")


# ---------------------------------------------------------------------------
# URI normalization — RFC 3986 §6.2.2 syntax-based normalization
# ---------------------------------------------------------------------------

_UNRESERVED = frozenset(
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
)
_DEFAULT_PORTS = {'http': 80, 'https': 443}


def _decode_unreserved(s: str) -> str:
    """Decode percent-encoded unreserved characters (RFC 3986 §2.3).

    Only decodes %XX where the decoded char is unreserved (letters, digits,
    ``-._~``). Reserved percent-encodings are kept with uppercase hex digits.
    """
    if '%' not in s:
        return s
    result: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == '%' and i + 2 < len(s):
            hex_str = s[i + 1:i + 3]
            try:
                char = chr(int(hex_str, 16))
                if char in _UNRESERVED:
                    result.append(char)
                else:
                    result.append(f'%{hex_str.upper()}')
                i += 3
                continue
            except ValueError:
                pass
        result.append(s[i])
        i += 1
    return ''.join(result)


def _resolve_dot_segments(path: str) -> str:
    """Remove dot segments from a URI path (RFC 3986 §5.2.4)."""
    segments = path.split('/')
    output: list[str] = []
    for seg in segments:
        if seg == '.':
            continue
        elif seg == '..':
            if output and output[-1] != '':
                output.pop()
        else:
            output.append(seg)
    resolved = '/'.join(output)
    if path.startswith('/') and not resolved.startswith('/'):
        resolved = '/' + resolved
    return resolved


def _normalize_http_uri(uri: str) -> str:
    """RFC 3986 §6.2.2 syntax-based normalization for HTTP/HTTPS URIs."""
    parsed = urlparse(uri)

    scheme = parsed.scheme.lower()
    host = (parsed.hostname or '').lower()

    port = parsed.port
    if port and port == _DEFAULT_PORTS.get(scheme):
        port = None
    netloc = f'{host}:{port}' if port else host
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f':{parsed.password}'
        netloc = f'{userinfo}@{netloc}'

    path = _resolve_dot_segments(_decode_unreserved(parsed.path))
    if not path:
        path = '/'

    query = _decode_unreserved(parsed.query)
    fragment = _decode_unreserved(parsed.fragment)

    return urlunparse((scheme, netloc, path, parsed.params, query, fragment))


def normalize_id(id: str) -> str:
    """Validate and normalize a document ID.

    For HTTP/HTTPS URIs, applies RFC 3986 §6.2.2 safe normalizations
    so that equivalent URIs map to the same document ID.
    For all other IDs, validates only.

    Returns the (possibly normalized) ID.
    Raises ValueError for invalid IDs.
    """
    validate_id(id)
    if id[:8].lower().startswith(('http://', 'https://')):
        id = _normalize_http_uri(id)
    return id


def casefold_tags(tags: dict[str, str]) -> dict[str, str]:
    """Casefold tag keys for case-insensitive lookup, preserving values.

    System tags (prefixed with '_') are left untouched.
    Tag values retain their original case for display fidelity
    (e.g. artist=AC/DC, album=Bashed Out).
    """
    return {
        (k.casefold() if not k.startswith(SYSTEM_TAG_PREFIX) else k): v
        for k, v in tags.items()
    }


def casefold_tags_for_index(tags: dict[str, str]) -> dict[str, str]:
    """Casefold both tag keys and values for index storage (ChromaDB).

    Used for the search index where case-insensitive where-clause
    matching is needed.  The canonical (display) tags live in SQLite.
    """
    return {
        (k.casefold() if not k.startswith(SYSTEM_TAG_PREFIX) else k):
        (v.casefold() if not k.startswith(SYSTEM_TAG_PREFIX) else v)
        for k, v in tags.items()
    }


def filter_non_system_tags(tags: dict[str, str]) -> dict[str, str]:
    """
    Filter out any system tags (those starting with '_').

    Use this to ensure source tags and derived tags cannot
    overwrite system-managed values.
    """
    return {k: v for k, v in tags.items() if not k.startswith(SYSTEM_TAG_PREFIX)}


@dataclass(frozen=True)
class Item:
    """
    An item retrieved from the reflective memory store.
    
    This is a read-only snapshot. To modify an item, use api.put()
    which returns a new Item with updated values.
    
    Timestamps and other system metadata live in tags, not as explicit fields.
    This follows the "schema as data" principle.
    
    Attributes:
        id: URI or custom identifier for the item
        summary: Generated summary of the content
        tags: All tags (source, system, and generated combined)
        score: Similarity score (present only in search results)
    
    System tags (managed automatically, in tags dict):
        _created: ISO timestamp when first indexed
        _updated: ISO timestamp when last indexed
        _updated_date: Date portion for easier queries
        _accessed: ISO timestamp when last retrieved
        _accessed_date: Date portion for easier queries
        _content_type: MIME type if known
        _source: How content was obtained (uri, inline)
        _session: Session that last touched this item
    """
    id: str
    summary: str
    tags: dict[str, str] = field(default_factory=dict)
    score: Optional[float] = None
    changed: Optional[bool] = None  # True if content changed on put(), None for queries
    
    @property
    def created(self) -> str | None:
        """ISO timestamp when first indexed (from _created tag)."""
        return self.tags.get("_created")
    
    @property
    def updated(self) -> str | None:
        """ISO timestamp when last indexed (from _updated tag)."""
        return self.tags.get("_updated")

    @property
    def accessed(self) -> str | None:
        """ISO timestamp when last retrieved (from _accessed tag)."""
        return self.tags.get("_accessed")
    
    def __str__(self) -> str:
        score_str = f" [{self.score:.3f}]" if self.score is not None else ""
        return f"{self.id}{score_str}: {self.summary[:60]}..."


# ---------------------------------------------------------------------------
# ItemContext — assembled display context for a single item
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimilarRef:
    """A similar item reference for display."""
    id: str
    offset: int           # version offset (0 = current)
    score: float | None
    date: str             # local date string
    summary: str


@dataclass(frozen=True)
class MetaRef:
    """A meta-doc reference for display."""
    id: str
    summary: str


@dataclass(frozen=True)
class VersionRef:
    """A version reference for navigation."""
    offset: int           # absolute offset (1 = previous, 2 = two ago)
    date: str
    summary: str


@dataclass(frozen=True)
class PartRef:
    """A part reference for display."""
    part_num: int
    summary: str
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class ItemContext:
    """Complete display context for a single item.

    Assembled by Keeper.get_context(), consumed by CLI renderer and
    REST serialization.  This is the wire format — JSON-serializable,
    shared between local CLI, remote CLI, and REST API.
    """
    item: Item
    viewing_offset: int = 0               # 0 = current version
    similar: list[SimilarRef] = field(default_factory=list)
    meta: dict[str, list[MetaRef]] = field(default_factory=dict)
    parts: list[PartRef] = field(default_factory=list)
    focus_part: int | None = None
    expand_parts: bool = False            # show all parts (no windowing)
    prev: list[VersionRef] = field(default_factory=list)
    next: list[VersionRef] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to JSON-ready dict."""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ItemContext":
        """Deserialize from JSON dict."""
        item = Item(**d.pop("item"))
        similar = [SimilarRef(**s) for s in d.pop("similar", [])]
        meta = {
            k: [MetaRef(**m) for m in v]
            for k, v in d.pop("meta", {}).items()
        }
        parts = [PartRef(**p) for p in d.pop("parts", [])]
        prev = [VersionRef(**v) for v in d.pop("prev", [])]
        nxt = [VersionRef(**v) for v in d.pop("next", [])]
        return cls(
            item=item, similar=similar, meta=meta,
            parts=parts, prev=prev, next=nxt, **d,
        )


# ---------------------------------------------------------------------------
# PromptResult — rendered agent prompt with injected context
# ---------------------------------------------------------------------------


@dataclass
class PromptResult:
    """Rendered agent prompt with context injection.

    The ``prompt`` field is a template that may contain ``{get}`` and
    ``{find}`` placeholders.  The CLI/MCP renderer expands these with
    the rendered ``context`` and ``search_results``.
    """
    context: ItemContext | None           # from get_context(id) — default "now"
    search_results: list[Item] | None     # from find(query=text, ...) when text given
    prompt: str                            # the ## Prompt section (may contain {get}/{find})


@dataclass(frozen=True)
class PromptInfo:
    """Summary info for an available agent prompt."""
    name: str            # e.g. "reflect"
    summary: str         # first line of doc body
