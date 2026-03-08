"""Validation for system documents with parser-based semantics.

System docs (.tag/*, .meta/*, .prompt/*) have structured content that
keep parses and interprets at runtime.  Malformed docs fail silently —
a broken tag spec stops classifying, a bad meta doc returns no results.

This module provides upfront validation: parse the doc, check structure,
report diagnostics.  Validators mirror the real parsers — they check
exactly what the runtime checks, so validation passing means runtime
will accept the doc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Diagnostic:
    """A single validation finding."""

    severity: str  # "error", "warning", "info"
    message: str
    location: str = ""  # e.g. "line 3" or "## Prompt section"

    def __str__(self) -> str:
        loc = f" ({self.location})" if self.location else ""
        return f"[{self.severity}]{loc} {self.message}"


@dataclass
class ValidationResult:
    """Collected diagnostics from validating a system doc."""

    doc_id: str
    doc_type: str  # "tag", "meta", "prompt", "unknown"
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


# ---------------------------------------------------------------------------
# Regex patterns (mirrored from api.py and analyzers.py)
# ---------------------------------------------------------------------------

_META_QUERY_PAIR = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*=\S+$")
_META_CONTEXT_KEY = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)=$")
_META_PREREQ_KEY = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)=\*$")
_PROMPT_SECTION_RE = re.compile(
    r"^## Prompt\s*\n(.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def validate_system_doc(
    doc_id: str,
    content: str,
    tags: dict[str, Any] | None = None,
) -> ValidationResult:
    """Validate a system doc by ID prefix dispatch.

    Args:
        doc_id: The document ID (e.g. ".tag/act", ".meta/related").
        content: The document body (summary field content).
        tags: The document's tags dict.

    Returns:
        ValidationResult with diagnostics.
    """
    tags = tags or {}

    if doc_id.startswith(".tag/"):
        return _validate_tag_doc(doc_id, content, tags)
    if doc_id.startswith(".meta/"):
        return _validate_meta_doc(doc_id, content, tags)
    if doc_id.startswith(".prompt/"):
        return _validate_prompt_doc(doc_id, content, tags)

    return ValidationResult(
        doc_id=doc_id,
        doc_type="unknown",
        diagnostics=[Diagnostic("info", f"no validator for doc type: {doc_id}")],
    )


# ---------------------------------------------------------------------------
# .tag/* validator
# ---------------------------------------------------------------------------

def _validate_tag_doc(
    doc_id: str,
    content: str,
    tags: dict[str, Any],
) -> ValidationResult:
    result = ValidationResult(doc_id=doc_id, doc_type="tag")
    parts = doc_id.split("/")

    if len(parts) < 2 or not parts[1]:
        result.diagnostics.append(
            Diagnostic("error", "tag doc ID must be .tag/{key} or .tag/{key}/{value}")
        )
        return result

    is_parent = len(parts) == 2
    is_value = len(parts) == 3

    if len(parts) > 3:
        result.diagnostics.append(
            Diagnostic("error", f"tag doc ID too deep: {doc_id} (max: .tag/key/value)")
        )
        return result

    # Validate tag key format
    key = parts[1]
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
        result.diagnostics.append(
            Diagnostic("error", f"invalid tag key {key!r}: must be identifier (letters, digits, underscores)")
        )

    if is_value:
        value = parts[2]
        if not value:
            result.diagnostics.append(
                Diagnostic("error", "tag value name cannot be empty")
            )

    if not content.strip():
        result.diagnostics.append(
            Diagnostic("warning", "empty content — tag has no description")
        )

    if is_parent:
        _validate_tag_parent(result, content, tags)
    elif is_value:
        _validate_tag_value(result, content, tags)

    return result


def _validate_tag_parent(
    result: ValidationResult,
    content: str,
    tags: dict[str, Any],
) -> None:
    """Validate a parent tag spec (.tag/key)."""
    constrained = tags.get("_constrained")
    singular = tags.get("_singular")
    inverse = tags.get("_inverse")

    if constrained == "true":
        # Constrained tags should have a ## Prompt section for classification
        prompt = _PROMPT_SECTION_RE.search(content)
        if not prompt or not prompt.group(1).strip():
            result.diagnostics.append(
                Diagnostic(
                    "warning",
                    "constrained tag spec has no ## Prompt section — classifier will use summary as fallback",
                    "## Prompt section",
                )
            )

    if inverse is not None:
        if not isinstance(inverse, str) or not inverse.strip():
            result.diagnostics.append(
                Diagnostic("error", "_inverse tag must be a non-empty string")
            )
        elif not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", str(inverse).strip()):
            result.diagnostics.append(
                Diagnostic(
                    "warning",
                    f"_inverse value {inverse!r} is not a valid tag key identifier",
                )
            )

    if singular is not None and singular != "true":
        result.diagnostics.append(
            Diagnostic("warning", f"_singular should be 'true' or absent, got {singular!r}")
        )

    if constrained is not None and constrained != "true":
        result.diagnostics.append(
            Diagnostic("warning", f"_constrained should be 'true' or absent, got {constrained!r}")
        )


def _validate_tag_value(
    result: ValidationResult,
    content: str,
    tags: dict[str, Any],
) -> None:
    """Validate a tag value doc (.tag/key/value)."""
    # Value docs can have a ## Prompt section for classification detail
    prompt = _PROMPT_SECTION_RE.search(content)
    if prompt and not prompt.group(1).strip():
        result.diagnostics.append(
            Diagnostic("warning", "## Prompt section is present but empty")
        )


# ---------------------------------------------------------------------------
# .meta/* validator
# ---------------------------------------------------------------------------

def _validate_meta_doc(
    doc_id: str,
    content: str,
    tags: dict[str, Any],
) -> ValidationResult:
    result = ValidationResult(doc_id=doc_id, doc_type="meta")
    parts = doc_id.split("/")

    if len(parts) < 2 or not parts[1]:
        result.diagnostics.append(
            Diagnostic("error", "meta doc ID must be .meta/{name}")
        )
        return result

    if not content.strip():
        result.diagnostics.append(
            Diagnostic("error", "empty content — meta doc has no query rules")
        )
        return result

    query_count = 0
    context_count = 0
    prereq_count = 0
    suspect_lines = []

    for line_num, raw_line in enumerate(content.split("\n"), start=1):
        line = raw_line.strip()
        if not line:
            continue

        # Skip markdown headers, frontmatter delimiters, and comments
        if line.startswith("#") or line.startswith("---"):
            continue

        if _META_PREREQ_KEY.match(line):
            prereq_count += 1
            continue

        if _META_CONTEXT_KEY.match(line):
            context_count += 1
            continue

        # Try as query line (space-separated key=value pairs)
        tokens = line.split()
        is_query = True
        for token in tokens:
            if not _META_QUERY_PAIR.match(token):
                is_query = False
                break

        if is_query and tokens:
            query_count += 1
        elif "=" in line:
            # Line contains = but doesn't parse — likely a malformed rule
            suspect_lines.append((line_num, line))
        # else: pure prose line, matches runtime behavior (silently skipped)

    if not query_count and not context_count and not prereq_count:
        result.diagnostics.append(
            Diagnostic("warning", "no valid query rules found — meta doc will match nothing")
        )

    for line_num, line in suspect_lines:
        result.diagnostics.append(
            Diagnostic("warning", f"possible malformed query rule: {line!r}", f"line {line_num}")
        )

    return result


# ---------------------------------------------------------------------------
# .prompt/* validator
# ---------------------------------------------------------------------------

def _validate_prompt_doc(
    doc_id: str,
    content: str,
    tags: dict[str, Any],
) -> ValidationResult:
    result = ValidationResult(doc_id=doc_id, doc_type="prompt")
    parts = doc_id.split("/")

    if len(parts) < 3 or not parts[1] or not parts[2]:
        result.diagnostics.append(
            Diagnostic("error", "prompt doc ID must be .prompt/{prefix}/{name}")
        )
        return result

    prefix = parts[1]
    known_prefixes = {"analyze", "summarize", "agent", "reflect", "review", "tag"}
    if prefix not in known_prefixes:
        result.diagnostics.append(
            Diagnostic("info", f"prompt prefix {prefix!r} is not a known built-in prefix")
        )

    if not content.strip():
        result.diagnostics.append(
            Diagnostic("error", "empty content — prompt doc has no content")
        )
        return result

    # Must have ## Prompt section
    prompt_match = _PROMPT_SECTION_RE.search(content)
    if not prompt_match:
        result.diagnostics.append(
            Diagnostic("error", "missing ## Prompt section — prompt doc will be skipped at runtime")
        )
        return result

    prompt_text = prompt_match.group(1).strip()
    if not prompt_text:
        result.diagnostics.append(
            Diagnostic("error", "## Prompt section is empty — prompt doc will be skipped at runtime")
        )
        return result

    # Validate match rules (content before ## Prompt, same syntax as .meta/*)
    prompt_start = prompt_match.start()
    preamble = content[:prompt_start].strip()
    if preamble:
        _validate_prompt_match_rules(result, preamble)

    return result


def _validate_prompt_match_rules(
    result: ValidationResult,
    preamble: str,
) -> None:
    """Validate the match-rule section of a prompt doc (before ## Prompt).

    Prose lines are expected (descriptions, documentation). Only lines
    containing ``=`` that don't parse as valid rules are flagged.
    """
    rule_count = 0
    for line_num, raw_line in enumerate(preamble.split("\n"), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("---"):
            continue

        # Same parsing as meta docs
        if _META_PREREQ_KEY.match(line):
            rule_count += 1
            continue
        if _META_CONTEXT_KEY.match(line):
            rule_count += 1
            continue

        tokens = line.split()
        is_query = True
        for token in tokens:
            if not _META_QUERY_PAIR.match(token):
                is_query = False
                break
        if is_query and tokens:
            rule_count += 1
        elif "=" in line:
            # Contains = but doesn't parse — likely a malformed rule
            result.diagnostics.append(
                Diagnostic("warning", f"possible malformed match rule: {line!r}", f"line {line_num}")
            )
        # else: prose line, expected and harmless
