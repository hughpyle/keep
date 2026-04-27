"""VirusTotal URL assessment action.

Lookup-only: queries VT for existing analysis results but never submits
unknown URLs for scanning. This avoids disclosing arbitrary URLs to a
third-party service as a side effect of normal note ingestion — especially
important for links extracted from private mail or documents.

Results are cached locally in a SQLite DB (~/.keep/actions/) with a 24h TTL
to stay within VT free-tier rate limits (4 req/min).

Requires VIRUSTOTAL_API_KEY or VT_API_KEY in the environment. When absent,
returns assessment="disabled" and the caller proceeds normally.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from keep.paths import get_config_dir
from keep.providers.http import http_session

from . import action

logger = logging.getLogger(__name__)

VT_URL_INFO_ENDPOINT = "https://www.virustotal.com/api/v3/urls/{id}"
VT_ASSESSMENT_TAG = "assessment_virustotal"
VT_CHECKED_AT_TAG = "assessment_virustotal_checked_at"
VT_CACHE_TTL_SECONDS = 24 * 60 * 60  # re-check after 24 hours
VT_HTTP_TIMEOUT_SECONDS = 5  # fail fast if VT is unreachable


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _effective_target_url(params: dict[str, Any]) -> str | None:
    """Extract the assessable URL from params.

    Prefers target_uri (set by put) over target_id (set by stub, where
    the ID itself is typically the URL).
    """
    for candidate in (params.get("target_uri"), params.get("target_id")):
        if _is_http_url(candidate):
            return str(candidate).strip()
    return None


def _normalized_tags(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _default_result(params: dict[str, Any], *, assessment: str = "clean") -> dict[str, Any]:
    """Build a pass-through result that preserves all caller params.

    The assess state doc returns this shape to put/stub. Fields like id,
    uri, content, and tags pass through unchanged unless the assessment
    rewrites them (e.g., malicious replaces content with an explanation).
    """
    return {
        "assessment": assessment,
        "id": params.get("id"),
        "uri": params.get("uri"),
        "content": params.get("content"),
        "tags": _normalized_tags(params.get("tags")),
        "summary": params.get("summary"),
        "created_at": params.get("created_at"),
        "force": bool(params.get("force", False)),
        "queue_background_tasks": params.get("queue_background_tasks"),
        "stop_processing": False,
        "skip_fetch": False,
    }


# ---------------------------------------------------------------------------
# Local result cache — avoids redundant VT lookups and rate-limit pressure.
# Stored in ~/.keep/actions/assess_virustotal.sqlite3, one row per URL.
# Expired entries are deleted on read; upsert on write.
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    return get_config_dir() / "actions" / "assess_virustotal.sqlite3"


def _cache_connect() -> sqlite3.Connection:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS url_assessments (
            url TEXT PRIMARY KEY,
            assessment TEXT NOT NULL,
            malicious INTEGER NOT NULL,
            suspicious INTEGER NOT NULL,
            examples_json TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )
    return conn


def _cache_get(target_url: str) -> dict[str, Any] | None:
    """Return cached verdict if fresh, or None. Deletes expired entries."""
    try:
        with _cache_connect() as conn:
            row = conn.execute(
                """
                SELECT assessment, malicious, suspicious, examples_json, checked_at, expires_at
                FROM url_assessments
                WHERE url = ?
                """,
                (target_url,),
            ).fetchone()
            if row is None:
                return None
            if int(row[5]) <= int(time.time()):
                conn.execute("DELETE FROM url_assessments WHERE url = ?", (target_url,))
                conn.commit()
                return None
            return {
                "assessment": str(row[0]),
                "malicious": int(row[1]),
                "suspicious": int(row[2]),
                "examples": json.loads(row[3]),
                "checked_at": str(row[4]),
            }
    except Exception:
        logger.debug("VirusTotal cache read failed", exc_info=True)
        return None


def _cache_put(
    target_url: str,
    *,
    assessment: str,
    malicious: int,
    suspicious: int,
    examples: list[str],
    checked_at: str,
) -> None:
    """Store a verdict with a TTL. Only caches definite results."""
    if assessment not in {"ok", "suspicious", "malicious"}:
        return
    try:
        expires_at = int(time.time()) + VT_CACHE_TTL_SECONDS
        with _cache_connect() as conn:
            conn.execute(
                """
                INSERT INTO url_assessments (
                    url, assessment, malicious, suspicious, examples_json, checked_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    assessment = excluded.assessment,
                    malicious = excluded.malicious,
                    suspicious = excluded.suspicious,
                    examples_json = excluded.examples_json,
                    checked_at = excluded.checked_at,
                    expires_at = excluded.expires_at
                """,
                (
                    target_url,
                    assessment,
                    malicious,
                    suspicious,
                    json.dumps(examples, ensure_ascii=False),
                    checked_at,
                    expires_at,
                ),
            )
            conn.commit()
    except Exception:
        logger.debug("VirusTotal cache write failed", exc_info=True)


# ---------------------------------------------------------------------------
# VT API helpers
# ---------------------------------------------------------------------------


def _vt_api_key() -> str | None:
    for name in ("VIRUSTOTAL_API_KEY", "VT_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def _url_identifier(target_url: str) -> str:
    """VT v3 URL identifier: unpadded base64url of the target URL."""
    return base64.urlsafe_b64encode(target_url.encode("utf-8")).decode("ascii").rstrip("=")


def _extract_examples(results: dict[str, Any]) -> list[str]:
    """Pick up to 3 engine verdicts to show in the explanatory note."""
    examples: list[str] = []
    for engine_key, raw in results.items():
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or "").strip().lower()
        if category not in {"malicious", "suspicious"}:
            continue
        engine_name = str(raw.get("engine_name") or engine_key or "").strip()
        result = str(raw.get("result") or category).strip()
        label = f"{engine_name}: {result}" if engine_name else result
        examples.append(label)
        if len(examples) >= 3:
            break
    return examples


# ---------------------------------------------------------------------------
# Result formatting — what the user sees on a blocked or flagged note.
# ---------------------------------------------------------------------------


def _format_summary(target_url: str, *, assessment: str, malicious: int, suspicious: int) -> str:
    return (
        f"VirusTotal assessed {target_url} as {assessment} "
        f"({malicious} malicious, {suspicious} suspicious)"
    )


def _format_blocked_body(
    target_url: str,
    *,
    malicious: int,
    suspicious: int,
    examples: list[str],
) -> tuple[str, str]:
    """Build explanatory content for a malicious URL note.

    Returns (summary, body). The body replaces whatever the caller would
    have stored — the actual URI is never fetched.
    """
    summary = _format_summary(
        target_url,
        assessment="malicious",
        malicious=malicious,
        suspicious=suspicious,
    )
    lines = [
        summary,
        "",
        f"URL: {target_url}",
    ]
    if examples:
        lines.extend(["", "Examples:", *[f"- {example}" for example in examples]])
    return summary, "\n".join(lines)


def _merge_assessment_tags(
    original_tags: dict[str, Any],
    *,
    assessment: str,
    checked_at: str,
) -> dict[str, Any]:
    """Add VT-specific tags alongside the caller's original tags."""
    tags = dict(original_tags)
    tags[VT_ASSESSMENT_TAG] = assessment
    tags[VT_CHECKED_AT_TAG] = checked_at
    return tags


def _lookup_virustotal(target_url: str, api_key: str) -> dict[str, Any] | None:
    """Query VT for an existing analysis of target_url.

    Returns a verdict dict on success, None on network/API errors.
    Never submits the URL for scanning — only looks up prior results.
    Results are cached locally to reduce API calls.

    HTTP status handling:
      404  — URL not known to VT, return None (caller treats as clean)
      4xx  — auth/rate-limit issue, log and return None
      5xx  — VT server error, log and return None
    """
    cached = _cache_get(target_url)
    if cached is not None:
        return cached

    try:
        response = http_session().get(
            VT_URL_INFO_ENDPOINT.format(id=_url_identifier(target_url)),
            headers={"x-apikey": api_key},
            timeout=VT_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("VirusTotal lookup failed for %s", target_url, exc_info=True)
        return None

    if response.status_code == 404:
        return None
    if response.status_code in {400, 401, 403, 429} or response.status_code >= 500:
        logger.warning(
            "VirusTotal lookup unavailable for %s: HTTP %s",
            target_url,
            response.status_code,
        )
        return None

    response.raise_for_status()
    payload = response.json()
    attrs = payload.get("data", {}).get("attributes", {})
    if not isinstance(attrs, dict):
        return None

    # Derive verdict from VT's aggregate engine stats.
    stats = attrs.get("last_analysis_stats", {})
    results = attrs.get("last_analysis_results", {})
    malicious = int((stats or {}).get("malicious") or 0)
    suspicious = int((stats or {}).get("suspicious") or 0)
    if malicious > 0:
        assessment = "malicious"
    elif suspicious > 0:
        assessment = "suspicious"
    else:
        assessment = "ok"
    checked_at = datetime.now(timezone.utc).isoformat()
    examples = _extract_examples(results if isinstance(results, dict) else {})
    verdict = {
        "assessment": assessment,
        "malicious": malicious,
        "suspicious": suspicious,
        "examples": examples,
        "checked_at": checked_at,
    }
    _cache_put(
        target_url,
        assessment=assessment,
        malicious=malicious,
        suspicious=suspicious,
        examples=examples,
        checked_at=checked_at,
    )
    return verdict


@action(id="assess_virustotal")
class AssessVirusTotal:
    """Assess URL targets via VirusTotal and return final write directives.

    This action is invoked by the .state/assess/virustotal fragment.
    It receives all caller params (from put or stub) and returns
    the same shape — possibly rewritten for malicious URLs:

      ok         → pass through unchanged, add no tags
      suspicious → pass through unchanged, add VT tags
      malicious  → rewrite content/summary to an explanation,
                    suppress URI fetch and background processing
      disabled   → no API key configured, pass through unchanged
      unknown    → VT lookup failed or URL not in VT database
    """

    def run(self, params: dict[str, Any], context) -> dict[str, Any]:
        del context  # unused; assessment is intentionally self-contained

        result = _default_result(params)
        api_key = _vt_api_key()
        if not api_key:
            result["assessment"] = "disabled"
            return result

        target_url = _effective_target_url(params)
        if target_url is None:
            # Not an HTTP URL — nothing to assess (e.g., a person name stub).
            return result

        verdict = _lookup_virustotal(target_url, api_key)
        if verdict is None:
            # Network error or URL unknown to VT — let the write proceed.
            result["assessment"] = "unknown"
            return result

        assessment = str(verdict["assessment"])
        checked_at = str(verdict["checked_at"])
        malicious = int(verdict["malicious"])
        suspicious = int(verdict["suspicious"])
        examples = list(verdict["examples"])
        result["assessment"] = assessment

        if assessment == "malicious":
            result["tags"] = _merge_assessment_tags(
                result["tags"],
                assessment=assessment,
                checked_at=checked_at,
            )
            # Rewrite the note to an explanatory stub — never fetch the URL.
            summary, body = _format_blocked_body(
                target_url,
                malicious=malicious,
                suspicious=suspicious,
                examples=examples,
            )
            result.update(
                {
                    "id": result.get("id") or target_url,
                    "uri": None,
                    "content": body,
                    "summary": summary,
                    "queue_background_tasks": False,
                    "stop_processing": True,
                    "skip_fetch": True,
                }
            )
            return result

        if assessment == "suspicious":
            result["tags"] = _merge_assessment_tags(
                result["tags"],
                assessment=assessment,
                checked_at=checked_at,
            )

        # ok or suspicious — proceed normally. Only non-benign outcomes
        # are tagged on the note.
        result["stop_processing"] = False
        result["skip_fetch"] = False
        return result
