"""Global store-level ignore patterns.

The ``.ignore`` system doc contains fnmatch glob patterns (one per line)
that are excluded from all directory walks and watches.  This module
provides parsing, merging, and matching helpers.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath
from typing import Optional


def parse_ignore_patterns(text: str) -> list[str]:
    """Parse ``.ignore`` doc content into a list of glob patterns.

    One pattern per line.  Lines starting with ``#`` are comments.
    Blank lines and leading/trailing whitespace are ignored.
    """
    patterns: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def merge_excludes(
    global_patterns: list[str],
    local_patterns: Optional[list[str]],
) -> list[str]:
    """Merge global ``.ignore`` patterns with per-watch/per-put excludes.

    Returns a deduplicated combined list (global first, local appended).
    """
    if not global_patterns and not local_patterns:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for pat in list(global_patterns or []) + list(local_patterns or []):
        if pat not in seen:
            seen.add(pat)
            result.append(pat)
    return result


def _is_uri_pattern(pattern: str) -> bool:
    """Return True if *pattern* is a URI-scheme pattern (e.g. ``git://x/*``)."""
    idx = pattern.find("://")
    if idx < 1:
        return False
    # Scheme part must be free of glob chars
    scheme = pattern[:idx]
    return not any(ch in scheme for ch in ("*", "?", "["))


def uri_pattern_prefixes(patterns: list[str]) -> list[str]:
    """Extract queryable ID prefixes from URI-scheme patterns.

    For ``git://x-access-token/*`` the prefix is ``git://x-access-token/``.
    Returned prefixes are deduplicated.
    """
    seen: set[str] = set()
    result: list[str] = []
    for pat in patterns:
        if not _is_uri_pattern(pat):
            continue
        prefix_chars: list[str] = []
        for ch in pat:
            if ch in ("*", "?", "["):
                break
            prefix_chars.append(ch)
        prefix = "".join(prefix_chars)
        if prefix and prefix not in seen:
            seen.add(prefix)
            result.append(prefix)
    return result


def match_ignore(uri: str, patterns: list[str]) -> bool:
    """Test if an item ID matches any ignore pattern.

    Two pattern types are supported:

    * **URI patterns** (contain ``://`` in the scheme portion):
      fnmatch the full item ID against the pattern.
    * **File-path patterns** (everything else):
      match against ``file://`` URI path suffixes via :func:`match_file_uri`.
    """
    if not patterns:
        return False

    # Check URI-scheme patterns against the full item ID
    uri_pats = [p for p in patterns if _is_uri_pattern(p)]
    for pat in uri_pats:
        if fnmatch.fnmatch(uri, pat):
            return True

    # Check file-path patterns via suffix matching (file:// URIs only)
    file_pats = [p for p in patterns if not _is_uri_pattern(p)]
    if file_pats and match_file_uri(uri, file_pats):
        return True

    return False


def match_file_uri(uri: str, patterns: list[str]) -> bool:
    """Test if a ``file://`` URI's path matches any ignore pattern.

    Extracts the path from the URI and tests each suffix window against
    the patterns with ``fnmatch``.  For example, ``dist/*`` matches
    ``file:///a/b/dist/bundle.js`` because the suffix ``dist/bundle.js``
    matches the pattern.

    Returns False for non-``file://`` URIs.
    """
    if not uri.startswith("file://"):
        return False
    if not patterns:
        return False

    path = uri.removeprefix("file://")
    parts = PurePosixPath(path).parts  # ('/', 'a', 'b', 'dist', 'bundle.js')

    # Test every suffix window: basename first, then progressively longer
    # e.g. for /a/b/dist/bundle.js: "bundle.js", "dist/bundle.js", "b/dist/bundle.js", ...
    for i in range(len(parts) - 1, 0, -1):
        suffix = "/".join(parts[i:])
        for pat in patterns:
            if fnmatch.fnmatch(suffix, pat):
                return True

    return False
