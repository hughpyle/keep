#!/usr/bin/env python3
"""Offline reconstruction spike against a real keep store.

Tests the hypothesis that a small multi-channel support set is
discoverable and more useful than simple top-k retrieval.

The harness is intentionally offline and heuristic. It does not define
the product interface. It gives us:

- self-supervised probes from versions, parts, and edges
- question probes from LoCoMo-style QA datasets
- bounded candidate-pool construction from existing channels
- an OMP-like greedy reconstruction loop with cheap residuals
- baseline comparison against semantic, FTS, fused search, and deep
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from keep.api import Keeper
from keep.result_stats import enrich_find_output
from keep.types import parse_utc_timestamp


TOKEN_RE = re.compile(r"[a-z0-9]+")
LOCOMO_REF_RE = re.compile(r"^D(\d+):(\d+)$")
QUESTION_WORDS = {"what", "when", "where", "who", "why", "how", "which"}
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "did", "do", "does", "for", "from",
    "go", "had", "has", "have", "how", "in", "into", "is", "it", "its", "kind",
    "likely", "of", "on", "or", "the", "their", "them", "to", "was", "were",
    "what", "when", "where", "which", "who", "why", "would",
}
QUERY_NOTE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


@dataclass
class Probe:
    family: str
    mode: str
    probe_id: str
    query: str
    target_ids: list[str]
    toward_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    id: str
    base_id: str
    kind: str
    summary: str
    tags: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None
    embedding: list[float] | None = None
    features: dict[str, float] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    hints: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupCandidate:
    """Candidate support bundle for grouped pursuit.

    Grouped pursuit is the next spike for `around` reconstruction.
    Instead of selecting one note at a time, it selects a small local
    neighborhood that explains signal across several basis dimensions.

    The fields stay generic on purpose:
    - `kind` says how the bundle was formed (session-local, edge-local...)
    - `member_ids` is the full induced local neighborhood in the current pool
    - `emit_ids` is the small subset that would actually be surfaced if
      the group is selected under the support budget
    - `base_ids`, `anchor_names`, and `focus_terms` let us track grouped
      residuals such as uncovered anchors/facets/sessions
    """

    group_id: str
    kind: str
    member_ids: list[str]
    emit_ids: list[str]
    base_ids: set[str] = field(default_factory=set)
    anchor_names: set[str] = field(default_factory=set)
    focus_terms: set[str] = field(default_factory=set)
    has_temporal: bool = False
    score_measurement: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupDemand:
    """A spike-local demand that a support bundle can explain.

    The Elhamifar representative-selection papers cast sparse subset
    selection as choosing a few rows that explain the rest of the data.
    For keep's `around` spike, the analogous "data points" are not raw
    notes. They are demands induced by the concern:

    - facet terms that should appear in the recovered neighborhood
    - anchor/supernode references that should be supported
    - temporal support when the question asks for when/sequence
    - a small number of high-mass local regions, so the solution does
      not collapse onto one dominant cluster

    A full DS3-style optimizer would solve a row-sparse assignment. This
    spike uses the same representation, but approximates the assignment
    greedily: at each step, choose the group that most reduces the
    remaining weighted demand cost.
    """

    demand_id: str
    kind: str
    label: str
    weight: float
    metadata: dict[str, Any] = field(default_factory=dict)


JUDGE_SYSTEM = """You evaluate whether a set of note supports is sufficient for a concern.
Be terse and concrete. Return strict JSON only with keys:
- sufficient: boolean
- missing_facets: array of short strings
- rationale: short string
"""


class OpenAIJudge:
    def __init__(self, model: str):
        import os
        from openai import OpenAI

        key = os.environ.get("KEEP_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OpenAI API key required for judged mode")
        self._client = OpenAI(api_key=key)
        self.model = model
        self._use_responses = model.startswith(("gpt-5", "o3", "o4"))
        self._fallback_model = "gpt-4.1-mini" if self._use_responses else None

    def generate(self, system: str, user: str, *, max_tokens: int = 400) -> str:
        text = self._generate_with_model(self.model, system, user, max_tokens=max_tokens)
        if text or not self._fallback_model:
            return text
        return self._generate_with_model(self._fallback_model, system, user, max_tokens=max_tokens)

    def _generate_with_model(self, model: str, system: str, user: str, *, max_tokens: int) -> str:
        if model.startswith(("gpt-5", "o3", "o4")):
            response = self._client.responses.create(
                model=model,
                instructions=system,
                input=user,
                max_output_tokens=max_tokens,
            )
            text = getattr(response, "output_text", None)
            return text or ""

        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").casefold())


def _token_set(text: str) -> set[str]:
    return set(_tokenize(text))


def _short_query(text: str, *, max_terms: int = 12) -> str:
    terms = _tokenize(text)
    if not terms:
        return (text or "").strip()
    return " ".join(terms[:max_terms])


def _base_id(id_value: str) -> str:
    if "@p" in id_value:
        return id_value.rsplit("@p", 1)[0]
    if "@v" in id_value:
        return id_value.rsplit("@v", 1)[0]
    return id_value


def _kind_of(id_value: str) -> str:
    if "@p" in id_value:
        return "part"
    if "@v" in id_value:
        return "version"
    return "document"


def _parse_version_id(id_value: str) -> tuple[str, int] | None:
    if "@v" not in id_value:
        return None
    base_id, raw_version = id_value.rsplit("@v", 1)
    if not raw_version.isdigit():
        return None
    return base_id, int(raw_version)


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for ax, bx in zip(a, b):
        dot += ax * bx
        na += ax * ax
        nb += bx * bx
    if na <= 0 or nb <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / math.sqrt(na * nb)))


def _term_overlap(query_terms: set[str], text: str) -> float:
    if not query_terms:
        return 0.0
    cand_terms = _token_set(text)
    if not cand_terms:
        return 0.0
    return len(query_terms & cand_terms) / max(len(query_terms), 1)


def _recency_score(updated_at: str | None, *, half_life_days: float = 30.0) -> float:
    if not updated_at:
        return 0.0
    try:
        dt = parse_utc_timestamp(updated_at)
    except Exception:
        return 0.0
    now = time.time()
    days = max((now - dt.timestamp()) / 86400.0, 0.0)
    return 0.5 ** (days / max(half_life_days, 1e-6))


def _weighted_score(features: dict[str, float], weights: dict[str, float]) -> float:
    return sum(max(features.get(name, 0.0), 0.0) * weight for name, weight in weights.items())


def _candidate_raw_score(cand: Candidate, weights: dict[str, float]) -> float:
    """Raw multi-basis score before residual/coverage adjustments."""
    return _weighted_score(cand.features, weights)


def _group_measurement_score(member_ids: list[str], raw_scores: dict[str, float]) -> float:
    """Aggregate local evidence with a gentle decay.

    The first few members should matter most. This keeps large bundles
    from winning purely on size while still letting coherent local
    neighborhoods beat isolated high-scoring atoms.
    """
    total = 0.0
    decay = 1.0
    for member_id in member_ids:
        total += raw_scores.get(member_id, 0.0) * decay
        decay *= 0.7
    return total


def _normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    max_score = max(scores.values(), default=0.0)
    if max_score <= 0.0:
        return {key: 0.0 for key in scores}
    return {key: value / max_score for key, value in scores.items()}


def _candidate_conv(cand: Candidate) -> str | None:
    conv = cand.tags.get("conv")
    return str(conv) if conv is not None else None


def _support_metrics(support_ids: list[str], target_ids: list[str]) -> dict[str, Any]:
    support_set = set(support_ids)
    target_set = set(target_ids)
    strict_hits = sorted(target_set & support_set)

    support_bases = {_base_id(i) for i in support_set}
    target_bases = {_base_id(i) for i in target_set}
    base_hits = sorted(target_bases & support_bases)

    return {
        "support_size": len(support_ids),
        "strict_hit": bool(strict_hits),
        "strict_recall": len(strict_hits) / max(len(target_set), 1),
        "base_hit": bool(base_hits),
        "base_recall": len(base_hits) / max(len(target_bases), 1),
        "strict_hits": strict_hits,
        "base_hits": base_hits,
    }


def _group_upper_bound_metrics(groups: list[GroupCandidate], target_ids: list[str]) -> dict[str, Any]:
    """Oracle-style coverage bounds for grouped reconstruction.

    These diagnostics separate three failure modes:
    - target not present in the candidate pool at all
    - target present in some induced group but not selected
    - target present in a group, but omitted from that group's emitted
      representative subset
    """
    target_set = set(target_ids)
    target_bases = {_base_id(target_id) for target_id in target_ids}

    member_support: set[str] = set()
    member_bases: set[str] = set()
    emit_support: set[str] = set()
    emit_bases: set[str] = set()
    groups_with_targets: list[dict[str, Any]] = []
    groups_with_target_bases: list[dict[str, Any]] = []

    for group in groups:
        members = set(group.member_ids)
        member_support |= members
        member_bases |= {_base_id(member_id) for member_id in members}

        emits = set(group.emit_ids)
        emit_support |= emits
        emit_bases |= {_base_id(member_id) for member_id in emits}

        strict_hits = sorted(target_set & members)
        base_hits = sorted(target_bases & {_base_id(member_id) for member_id in members})
        emit_strict_hits = sorted(target_set & emits)
        emit_base_hits = sorted(target_bases & {_base_id(member_id) for member_id in emits})
        if strict_hits:
            strict_hit_ranks = {
                target_id: group.member_ids.index(target_id) + 1
                for target_id in strict_hits
                if target_id in group.member_ids
            }
            emit_hit_ranks = {
                target_id: group.emit_ids.index(target_id) + 1
                for target_id in emit_strict_hits
                if target_id in group.emit_ids
            }
            groups_with_targets.append({
                "group_id": group.group_id,
                "kind": group.kind,
                "strict_hits": strict_hits,
                "emit_hits": emit_strict_hits,
                "emit_ids": list(group.emit_ids),
                "target_member_ranks": strict_hit_ranks,
                "target_emit_ranks": emit_hit_ranks,
                "member_head": list(group.member_ids[:8]),
            })
        if base_hits:
            groups_with_target_bases.append({
                "group_id": group.group_id,
                "kind": group.kind,
                "base_hits": base_hits,
                "emit_base_hits": emit_base_hits,
                "emit_ids": list(group.emit_ids),
                "member_head": list(group.member_ids[:8]),
            })

    exact_member_hits = sorted(target_set & member_support)
    exact_emit_hits = sorted(target_set & emit_support)
    base_member_hits = sorted(target_bases & member_bases)
    base_emit_hits = sorted(target_bases & emit_bases)
    return {
        "group_strict_hit": bool(exact_member_hits),
        "group_strict_recall": len(exact_member_hits) / max(len(target_set), 1),
        "group_base_hit": bool(base_member_hits),
        "group_base_recall": len(base_member_hits) / max(len(target_bases), 1),
        "emit_strict_hit": bool(exact_emit_hits),
        "emit_strict_recall": len(exact_emit_hits) / max(len(target_set), 1),
        "emit_base_hit": bool(base_emit_hits),
        "emit_base_recall": len(base_emit_hits) / max(len(target_bases), 1),
        "group_count": len(groups),
        "groups_with_targets": groups_with_targets[:8],
        "groups_with_target_bases": groups_with_target_bases[:8],
    }


def _candidate_pool_upper_bounds(
    candidates: dict[str, Candidate],
    probe: Probe,
    *,
    groups: list[GroupCandidate] | None = None,
    deep_ids: list[str] | None = None,
) -> dict[str, Any]:
    candidate_ids = set(candidates.keys())
    candidate_bases = {_base_id(candidate_id) for candidate_id in candidate_ids}
    target_set = set(probe.target_ids)
    target_bases = {_base_id(target_id) for target_id in probe.target_ids}

    exact_pool_hits = sorted(target_set & candidate_ids)
    base_pool_hits = sorted(target_bases & candidate_bases)
    diagnostics = {
        "pool_strict_hit": bool(exact_pool_hits),
        "pool_strict_recall": len(exact_pool_hits) / max(len(target_set), 1),
        "pool_base_hit": bool(base_pool_hits),
        "pool_base_recall": len(base_pool_hits) / max(len(target_bases), 1),
        "pool_strict_hits": exact_pool_hits,
        "pool_base_hits": base_pool_hits,
    }
    if groups is not None:
        diagnostics["group_upper_bounds"] = _group_upper_bound_metrics(groups, probe.target_ids)
    if deep_ids is not None:
        deep_set = set(deep_ids)
        deep_bases = {_base_id(deep_id) for deep_id in deep_ids}
        diagnostics["deep_only"] = {
            "strict_missing_from_pool": sorted(deep_set - candidate_ids)[:20],
            "base_missing_from_pool": sorted(deep_bases - candidate_bases)[:20],
            "deep_support_size": len(deep_ids),
        }
    return diagnostics


def _deep_support_ids(results) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for item in results:
        if item.id not in seen:
            ids.append(item.id)
            seen.add(item.id)
    for group in getattr(results, "deep_groups", {}).values():
        for item in group:
            if item.id not in seen:
                ids.append(item.id)
                seen.add(item.id)
    return ids


def _candidate_text(summary: str, tags: dict[str, Any]) -> str:
    tag_bits: list[str] = []
    for key, value in sorted((tags or {}).items()):
        if str(key).startswith("_"):
            continue
        if isinstance(value, list):
            value_text = " ".join(str(v) for v in value)
        else:
            value_text = str(value)
        tag_bits.append(f"{key} {value_text}")
    if tag_bits:
        return f"{summary}\n" + "\n".join(tag_bits)
    return summary


def _trim_text(text: str, max_chars: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    candidates = [text]
    if "```" in text:
        fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
        candidates.extend(fenced)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _store_cache_key(kp: Keeper) -> str:
    cfg = getattr(kp, "_config", None)
    path = getattr(cfg, "path", None)
    return str(path or "")


def _question_kind(question: str) -> str:
    q = question.strip().casefold()
    if q.startswith("when ") or " when " in q:
        return "temporal"
    if q.startswith("who ") or " identity" in q or "who is" in q or "what is " in q:
        return "identity"
    if q.startswith("where ") or " where " in q:
        return "location"
    return "fact"


def _focus_terms(question: str, anchors: list[str]) -> list[str]:
    anchor_terms = {t for name in anchors for t in _tokenize(name)}
    terms: list[str] = []
    for term in _tokenize(question):
        if term in STOPWORDS or term in QUESTION_WORDS or term in anchor_terms:
            continue
        if len(term) <= 2:
            continue
        if term not in terms:
            terms.append(term)
    return terms[:8]


def _load_conv_roster(kp: Keeper, conv: str) -> list[str]:
    cache_key = (_store_cache_key(kp), conv)
    cached = QUERY_NOTE_CACHE.get(cache_key)
    if cached is not None:
        return list(cached.get("roster", []))

    roster: list[str] = []
    for note in kp.list_items(tags={"conv": conv, "type": "session"}, limit=500):
        tags = note.tags or {}
        for key in ("speaker_a", "speaker_b", "speaker"):
            name = str(tags.get(key) or "").strip()
            if name and name not in roster:
                roster.append(name)
    QUERY_NOTE_CACHE[cache_key] = {"roster": roster}
    return roster


def _analyze_query_note(kp: Keeper, probe: Probe) -> dict[str, Any]:
    conv = probe.metadata.get("conv")
    if conv is None:
        return {
            "kind": _question_kind(probe.query),
            "anchors": [],
            "focus_terms": _focus_terms(probe.query, []),
            "conv": None,
        }
    conv_text = str(conv)
    roster = _load_conv_roster(kp, conv_text)
    query_text = probe.query
    lower_query = query_text.casefold()
    anchors = [name for name in roster if re.search(rf"\b{re.escape(name.casefold())}\b", lower_query)]
    return {
        "kind": _question_kind(query_text),
        "anchors": anchors,
        "focus_terms": _focus_terms(query_text, anchors),
        "conv": conv_text,
    }


def _sanitize_image_id(url: str) -> str:
    import hashlib

    if len(url) < 200 and re.match(r"^https?://[\w./-]+\.\w+$", url):
        return url
    return f"img:{hashlib.sha256(url.encode()).hexdigest()[:16]}"


def _probe_rows(doc_store, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in doc_store._execute(sql, params).fetchall()]


def _locomo_target_ids(
    conv: int | str,
    evidence_refs: list[str],
    *,
    image_notes: list[dict[str, Any]] | None = None,
) -> list[str]:
    target_ids: list[str] = []
    conv_text = str(conv)
    image_by_ref: dict[str, list[str]] = {}
    for note in image_notes or []:
        tags = note.get("tags") or {}
        dia_id = str(tags.get("dia_id") or "")
        note_conv = str(tags.get("conv") or "")
        if dia_id and note_conv == conv_text:
            image_by_ref.setdefault(dia_id, []).append(_sanitize_image_id(str(note.get("id") or "")))

    for ref in evidence_refs:
        m = LOCOMO_REF_RE.match(str(ref))
        if not m:
            continue
        session_num = int(m.group(1))
        turn_num = int(m.group(2))
        target_ids.append(f"conv{conv_text}-session{session_num}@v{turn_num}")
        for image_id in image_by_ref.get(str(ref), []):
            target_ids.append(image_id)
    return list(dict.fromkeys(target_ids))


def _qa_mode(category: str, target_ids: list[str], requested_mode: str) -> str:
    if requested_mode in {"around", "towards"}:
        return requested_mode
    if len(target_ids) <= 1 and category in {"single-hop", "temporal"}:
        return "towards"
    if len(target_ids) <= 1:
        return "towards"
    return "around"


def sample_qa_probes(
    dataset_path: Path,
    rng: random.Random,
    limit: int,
    *,
    mode: str,
    categories: set[str] | None = None,
) -> list[Probe]:
    rows = json.loads(dataset_path.read_text())
    prepared_dir = dataset_path.parent
    image_notes_path = prepared_dir / "image_notes.json"
    image_notes = json.loads(image_notes_path.read_text()) if image_notes_path.exists() else []

    indexed_rows = list(enumerate(rows))
    rng.shuffle(indexed_rows)
    probes: list[Probe] = []
    for idx, row in indexed_rows:
        category = str(row.get("category") or "")
        if categories and category not in categories:
            continue
        conv = row.get("conv")
        question = str(row.get("question") or "").strip()
        evidence_refs = [str(ref) for ref in (row.get("evidence_refs") or [])]
        target_ids = _locomo_target_ids(conv, evidence_refs, image_notes=image_notes)
        probe_mode = _qa_mode(category, target_ids, mode)
        toward_id = target_ids[0] if probe_mode == "towards" and target_ids else None
        target_sessions = sorted({_base_id(target_id) for target_id in target_ids})
        probes.append(Probe(
            family="qa",
            mode=probe_mode,
            probe_id=f"qa:{idx}:conv{conv}:{category}",
            query=question,
            target_ids=target_ids,
            toward_id=toward_id,
            metadata={
                "qa_index": idx,
                "conv": str(conv),
                "category": category,
                "answer": row.get("answer"),
                "evidence_refs": evidence_refs,
                "evidence_text": row.get("evidence_text", ""),
                "target_sessions": target_sessions,
            },
        ))
        if len(probes) >= limit:
            break
    return probes


def sample_version_probes(kp: Keeper, rng: random.Random, limit: int) -> list[Probe]:
    doc_store = kp._document_store
    coll = kp._resolve_doc_collection()
    rows = _probe_rows(
        doc_store,
        """
        SELECT v.id, v.version, v.summary
        FROM document_versions v
        WHERE v.collection = ?
          AND length(trim(v.summary)) >= 40
          AND (
            SELECT COUNT(1) FROM document_versions v2
            WHERE v2.collection = v.collection AND v2.id = v.id
          ) >= 2
        """,
        (coll,),
    )
    rng.shuffle(rows)
    probes: list[Probe] = []
    for row in rows:
        base_id = str(row["id"])
        version = int(row["version"])
        summary = str(row["summary"] or "").strip()
        if not summary:
            continue
        around = doc_store.list_versions_around(coll, base_id, version, radius=1)
        target_ids = [base_id, f"{base_id}@v{version}"]
        for info in around:
            target_ids.append(f"{base_id}@v{info.version}")
        target_ids = list(dict.fromkeys(target_ids))
        probes.append(Probe(
            family="version",
            mode="towards",
            probe_id=f"version:{base_id}@v{version}",
            query=_short_query(summary),
            target_ids=target_ids,
            toward_id=f"{base_id}@v{version}",
            metadata={"base_id": base_id, "version": version},
        ))
        if len(probes) >= limit:
            break
    return probes


def sample_part_probes(kp: Keeper, rng: random.Random, limit: int) -> list[Probe]:
    doc_store = kp._document_store
    coll = kp._resolve_doc_collection()
    rows = _probe_rows(
        doc_store,
        """
        SELECT p.id, p.part_num, p.summary
        FROM document_parts p
        WHERE p.collection = ?
          AND length(trim(p.summary)) >= 40
          AND (
            SELECT COUNT(1) FROM document_parts p2
            WHERE p2.collection = p.collection AND p2.id = p.id
          ) >= 3
        """,
        (coll,),
    )
    rng.shuffle(rows)
    probes: list[Probe] = []
    for row in rows:
        base_id = str(row["id"])
        part_num = int(row["part_num"])
        summary = str(row["summary"] or "").strip()
        if not summary:
            continue
        parts = doc_store.list_parts(coll, base_id)
        target_ids = [base_id, f"{base_id}@p{part_num}"]
        for info in parts:
            if abs(info.part_num - part_num) <= 1:
                target_ids.append(f"{base_id}@p{info.part_num}")
        target_ids = list(dict.fromkeys(target_ids))
        probes.append(Probe(
            family="part",
            mode="around",
            probe_id=f"part:{base_id}@p{part_num}",
            query=_short_query(summary),
            target_ids=target_ids,
            toward_id=None,
            metadata={"base_id": base_id, "part_num": part_num},
        ))
        if len(probes) >= limit:
            break
    return probes


def sample_edge_probes(kp: Keeper, rng: random.Random, limit: int) -> list[Probe]:
    doc_store = kp._document_store
    coll = kp._resolve_doc_collection()
    rows = _probe_rows(
        doc_store,
        """
        SELECT e.source_id, e.target_id, e.predicate, d.summary
        FROM edges e
        JOIN documents d
          ON d.collection = e.collection AND d.id = e.source_id
        WHERE e.collection = ?
          AND length(trim(d.summary)) >= 40
          AND e.source_id != e.target_id
        """,
        (coll,),
    )
    rng.shuffle(rows)
    probes: list[Probe] = []
    for row in rows:
        source_id = str(row["source_id"])
        target_id = str(row["target_id"])
        predicate = str(row["predicate"])
        summary = str(row["summary"] or "").strip()
        if not summary:
            continue
        probes.append(Probe(
            family="edge",
            mode="towards",
            probe_id=f"edge:{source_id}->{predicate}->{target_id}",
            query=_short_query(summary),
            target_ids=[source_id, target_id],
            toward_id=target_id,
            metadata={"source_id": source_id, "target_id": target_id, "predicate": predicate},
        ))
        if len(probes) >= limit:
            break
    return probes


def _fetch_candidate_record(kp: Keeper, id_value: str) -> tuple[str, dict[str, Any], str | None] | None:
    doc_store = kp._document_store
    coll = kp._resolve_doc_collection()
    if "@p" in id_value:
        base_id, raw_part = id_value.rsplit("@p", 1)
        if not raw_part.isdigit():
            return None
        info = doc_store.get_part(coll, base_id, int(raw_part))
        if info is None:
            return None
        return info.summary, dict(info.tags), info.created_at
    if "@v" in id_value:
        base_id, raw_version = id_value.rsplit("@v", 1)
        if not raw_version.isdigit():
            return None
        row = doc_store._execute(
            """
            SELECT summary, tags_json, created_at
            FROM document_versions
            WHERE id = ? AND collection = ? AND version = ?
            """,
            (base_id, coll, int(raw_version)),
        ).fetchone()
        if row is None:
            return None
        return str(row["summary"] or ""), json.loads(row["tags_json"] or "{}"), row["created_at"]
    doc = doc_store.get(coll, id_value)
    if doc is None:
        return None
    return doc.summary, dict(doc.tags), doc.updated_at


def _support_brief(kp: Keeper, support_ids: list[str], *, max_items: int = 8) -> list[dict[str, Any]]:
    brief: list[dict[str, Any]] = []
    for item_id in support_ids[:max_items]:
        fetched = _fetch_candidate_record(kp, item_id)
        if fetched is None:
            continue
        summary, tags, updated_at = fetched
        brief.append({
            "id": item_id,
            "base_id": _base_id(item_id),
            "kind": _kind_of(item_id),
            "summary": _trim_text(summary),
            "updated_at": updated_at,
            "tags": {k: v for k, v in (tags or {}).items() if not str(k).startswith("_")},
        })
    return brief


def _judge_support(
    judge: OpenAIJudge | None,
    probe: Probe,
    support_ids: list[str],
    kp: Keeper,
) -> dict[str, Any] | None:
    if judge is None:
        return None
    support = _support_brief(kp, support_ids)
    if not support:
        return {
            "sufficient": False,
            "missing_facets": ["no support surfaced"],
            "rationale": "No support notes were available to judge.",
        }
    user = json.dumps({
        "concern": probe.query,
        "probe_family": probe.family,
        "support": support,
    }, ensure_ascii=False, indent=2)
    try:
        raw = judge.generate(JUDGE_SYSTEM, user, max_tokens=400)
        if not raw:
            return None
        parsed = _parse_json_object(raw)
        if parsed is None:
            return {"error": f"judge returned non-JSON: {raw[:200]}"}
        return {
            "sufficient": bool(parsed.get("sufficient", False)),
            "missing_facets": list(parsed.get("missing_facets", []) or []),
            "rationale": str(parsed.get("rationale", "") or ""),
        }
    except Exception as e:
        return {
            "error": str(e),
        }


def _ensure_candidate(
    candidates: dict[str, Candidate],
    kp: Keeper,
    id_value: str,
    *,
    summary: str | None = None,
    tags: dict[str, Any] | None = None,
    updated_at: str | None = None,
    source: str | None = None,
) -> Candidate | None:
    cand = candidates.get(id_value)
    if cand is not None:
        if source and source not in cand.sources:
            cand.sources.append(source)
        return cand

    if summary is None or tags is None:
        fetched = _fetch_candidate_record(kp, id_value)
        if fetched is None:
            return None
        fetched_summary, fetched_tags, fetched_updated_at = fetched
        if summary is None:
            summary = fetched_summary
        if tags is None:
            tags = fetched_tags
        if updated_at is None:
            updated_at = fetched_updated_at

    cand = Candidate(
        id=id_value,
        base_id=_base_id(id_value),
        kind=_kind_of(id_value),
        summary=summary or "",
        tags=dict(tags or {}),
        updated_at=updated_at,
        sources=[source] if source else [],
    )
    candidates[id_value] = cand
    return cand


def _load_candidate_embeddings_batch(
    kp: Keeper,
    chroma_coll: str,
    candidates: dict[str, Candidate],
    *,
    ids: list[str] | None = None,
) -> int:
    """Populate candidate embeddings with one store round-trip.

    The spike originally fetched embeddings one candidate at a time,
    which dominated pool-construction cost. The underlying store already
    supports batch reads via `get_entries_full`, so use that surface
    directly and fill only the candidates that still need embeddings.
    """
    wanted_ids = ids or [cand.id for cand in candidates.values() if cand.embedding is None]
    wanted_ids = [cand_id for cand_id in wanted_ids if cand_id in candidates and candidates[cand_id].embedding is None]
    if not wanted_ids:
        return 0
    entries = kp._store.get_entries_full(chroma_coll, wanted_ids)
    loaded = 0
    for entry in entries:
        cand = candidates.get(str(entry.get("id")))
        if cand is None:
            continue
        embedding = entry.get("embedding")
        if embedding is None:
            continue
        cand.embedding = list(embedding)
        loaded += 1
    return loaded


def build_candidate_pool(
    kp: Keeper,
    probe: Probe,
    *,
    semantic_limit: int,
    fts_limit: int,
    seed_limit: int,
    edge_limit: int,
    version_limit: int,
    part_limit: int,
    query_note_model: bool,
) -> tuple[dict[str, Candidate], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    started_at = time.perf_counter()
    doc_store = kp._document_store
    store = kp._store
    chroma_coll = kp._resolve_chroma_collection()
    doc_coll = kp._resolve_doc_collection()
    query_terms = _token_set(probe.query)
    candidates: dict[str, Candidate] = {}
    semantic_rows: list[dict[str, Any]] = []
    fts_rows_json: list[dict[str, Any]] = []
    seed_version_hits: list[tuple[str, int, str, int]] = []
    timings_ms: dict[str, float] = {}
    counters: dict[str, int] = {
        "semantic_rows": 0,
        "fts_rows": 0,
        "anchor_names": 0,
        "anchor_session_bases": 0,
        "anchor_scoped_fts_rows": 0,
        "candidate_count_pre_features": 0,
        "candidate_embedding_fetches": 0,
        "candidate_embedding_batches": 0,
        "toward_embedding_fetches": 0,
    }

    def measure(label: str, fn):
        t0 = time.perf_counter()
        result = fn()
        timings_ms[label] = round((time.perf_counter() - t0) * 1000, 2)
        return result

    conv_tag = str(probe.metadata.get("conv")) if probe.metadata.get("conv") is not None else None
    target_sessions = set(str(x) for x in probe.metadata.get("target_sessions", []) if x)
    query_note = _analyze_query_note(kp, probe) if query_note_model else {
        "kind": _question_kind(probe.query),
        "anchors": [],
        "focus_terms": _focus_terms(probe.query, []),
        "conv": conv_tag,
    }

    query_embedding: list[float] | None = None
    if kp._config.embedding is not None:
        try:
            query_embedding = measure("query_embedding", lambda: kp._get_embedding_provider().embed(probe.query))
        except Exception as e:
            print(f"warning: semantic embedding failed for probe {probe.probe_id}: {e}", file=sys.stderr)

    if query_embedding is not None:
        sem_results = measure("semantic_query", lambda: store.query_embedding(chroma_coll, query_embedding, limit=semantic_limit))
        sem_items = measure("semantic_recency_decay", lambda: kp._apply_recency_decay([r.to_item() for r in sem_results]))
        for rank, item in enumerate(sem_items, start=1):
            cand = _ensure_candidate(
                candidates,
                kp,
                item.id,
                summary=item.summary,
                tags=item.tags,
                updated_at=item.tags.get("_updated"),
                source="semantic",
            )
            if cand is None:
                continue
            cand.features["semantic_seed"] = max(cand.features.get("semantic_seed", 0.0), item.score or 0.0)
            cand.features["semantic_rank"] = max(cand.features.get("semantic_rank", 0.0), 1.0 / (60 + rank))
            semantic_rows.append({"id": item.id, "score": item.score or 0.0})
            parsed_version = _parse_version_id(item.id)
            if parsed_version is not None:
                seed_version_hits.append((parsed_version[0], parsed_version[1], "semantic", rank))
        counters["semantic_rows"] = len(semantic_rows)

    raw_fts_rows = measure("fts_query", lambda: doc_store.query_fts(doc_coll, probe.query, limit=fts_limit))
    for rank, (item_id, summary, bm25_rank) in enumerate(raw_fts_rows, start=1):
        cand = _ensure_candidate(
            candidates,
            kp,
            item_id,
            summary=summary,
            updated_at=None,
            source="fts",
        )
        if cand is None:
            continue
        cand.features["fts_rank"] = max(cand.features.get("fts_rank", 0.0), 1.0 / (60 + rank))
        cand.features["fts_bm25"] = max(cand.features.get("fts_bm25", 0.0), 1.0 / (1.0 + abs(float(bm25_rank))))
        fts_rows_json.append({"id": item_id, "rank": float(bm25_rank)})
        parsed_version = _parse_version_id(item_id)
        if parsed_version is not None:
            seed_version_hits.append((parsed_version[0], parsed_version[1], "fts", rank))
    counters["fts_rows"] = len(fts_rows_json)

    if query_note_model:
        counters["anchor_names"] = len(query_note.get("anchors", []))
        anchor_base_ids: set[str] = set()
        def expand_anchors() -> None:
            for anchor in query_note.get("anchors", []):
                cand = _ensure_candidate(candidates, kp, anchor, source="anchor")
                if cand is not None:
                    cand.features["anchor_seed"] = 1.0

                for idx, (_inv, source_id, _created) in enumerate(doc_store.get_inverse_edges(doc_coll, anchor)[:edge_limit * 3], start=1):
                    cand = _ensure_candidate(candidates, kp, source_id, source=f"anchor-edge:{anchor}")
                    if cand is not None:
                        cand.features["anchor_edge"] = max(cand.features.get("anchor_edge", 0.0), 1.0 / idx)
                        anchor_base_ids.add(cand.base_id)

                for idx, (_pred, target_id, _created) in enumerate(doc_store.get_forward_edges(doc_coll, anchor)[:edge_limit], start=1):
                    cand = _ensure_candidate(candidates, kp, target_id, source=f"anchor-forward:{anchor}")
                    if cand is not None:
                        cand.features["anchor_edge"] = max(cand.features.get("anchor_edge", 0.0), 1.0 / idx)
                        anchor_base_ids.add(cand.base_id)
        measure("anchor_expansion", expand_anchors)
        if probe.mode == "around" and anchor_base_ids:
            counters["anchor_session_bases"] = len(anchor_base_ids)

            def anchor_version_probe() -> None:
                scoped_limit = max(len(anchor_base_ids) * max(version_limit, 2), 20)
                scoped_rows = doc_store.query_fts_scoped(
                    doc_coll,
                    probe.query,
                    sorted(anchor_base_ids),
                    limit=scoped_limit,
                )
                counters["anchor_scoped_fts_rows"] = len(scoped_rows)
                for rank, (item_id, summary, bm25_rank) in enumerate(scoped_rows, start=1):
                    cand = _ensure_candidate(
                        candidates,
                        kp,
                        item_id,
                        summary=summary,
                        updated_at=None,
                        source="anchor-scoped-fts",
                    )
                    if cand is None:
                        continue
                    cand.features["fts_rank"] = max(cand.features.get("fts_rank", 0.0), 1.0 / (60 + rank))
                    cand.features["fts_bm25"] = max(cand.features.get("fts_bm25", 0.0), 1.0 / (1.0 + abs(float(bm25_rank))))
                    cand.features["anchor_edge"] = max(cand.features.get("anchor_edge", 0.0), 0.4)
                    parsed_version = _parse_version_id(item_id)
                    if parsed_version is not None:
                        seed_version_hits.append((parsed_version[0], parsed_version[1], "fts", rank))
            measure("anchor_scoped_fts", anchor_version_probe)

    seed_scores: dict[str, float] = {}
    for cand in candidates.values():
        seed_scores[cand.base_id] = max(
            seed_scores.get(cand.base_id, 0.0),
            cand.features.get("semantic_rank", 0.0) + 1.5 * cand.features.get("fts_rank", 0.0),
        )
    seed_bases = [
        base_id for base_id, _score in sorted(seed_scores.items(), key=lambda t: t[1], reverse=True)[:seed_limit]
    ]

    def expand_seed_bases() -> None:
        for base_id in seed_bases:
            base_doc = doc_store.get(doc_coll, base_id)
            if base_doc is not None:
                _ensure_candidate(
                    candidates,
                    kp,
                    base_id,
                    summary=base_doc.summary,
                    tags=base_doc.tags,
                    updated_at=base_doc.updated_at,
                    source="seed",
                )

            for idx, (_pred, target_id, _created) in enumerate(doc_store.get_forward_edges(doc_coll, base_id)[:edge_limit], start=1):
                cand = _ensure_candidate(candidates, kp, target_id, source=f"edge:{base_id}")
                if cand is not None:
                    cand.features["edge"] = max(cand.features.get("edge", 0.0), 1.0 / idx)

            for idx, (_inv, source_id, _created) in enumerate(doc_store.get_inverse_edges(doc_coll, base_id)[:edge_limit], start=1):
                cand = _ensure_candidate(candidates, kp, source_id, source=f"edge:{base_id}")
                if cand is not None:
                    cand.features["edge"] = max(cand.features.get("edge", 0.0), 1.0 / idx)

            for idx, info in enumerate(doc_store.list_versions(doc_coll, base_id, limit=version_limit), start=1):
                cand = _ensure_candidate(
                    candidates,
                    kp,
                    f"{base_id}@v{info.version}",
                    summary=info.summary,
                    tags=info.tags,
                    updated_at=info.created_at,
                    source=f"version:{base_id}",
                )
                if cand is not None:
                    cand.features["version"] = max(cand.features.get("version", 0.0), 1.0 / idx)

            for idx, info in enumerate(doc_store.list_parts(doc_coll, base_id)[:part_limit], start=1):
                cand = _ensure_candidate(
                    candidates,
                    kp,
                    f"{base_id}@p{info.part_num}",
                    summary=info.summary,
                    tags=info.tags,
                    updated_at=info.created_at,
                    source=f"part:{base_id}",
                )
                if cand is not None:
                    cand.features["part"] = max(cand.features.get("part", 0.0), 1.0 / idx)
    measure("seed_expansion", expand_seed_bases)

    # Version-local expansion: when search directly hits a version, expand a
    # neighborhood around that specific version rather than only the newest few
    # archived versions for the base note.
    def expand_version_local() -> None:
        seen_version_roots: set[tuple[str, int]] = set()
        for base_id, version_num, source_kind, rank in seed_version_hits:
            root = (base_id, version_num)
            if root in seen_version_roots:
                continue
            seen_version_roots.add(root)
            around = doc_store.list_versions_around(doc_coll, base_id, version_num, radius=version_limit)
            for info in around:
                cand = _ensure_candidate(
                    candidates,
                    kp,
                    f"{base_id}@v{info.version}",
                    summary=info.summary,
                    tags=info.tags,
                    updated_at=info.created_at,
                    source=f"version-local:{base_id}@v{version_num}",
                )
                if cand is None:
                    continue
                distance = abs(info.version - version_num)
                locality = 1.0 / (1.0 + distance)
                cand.features["version_local"] = max(cand.features.get("version_local", 0.0), locality)
                if source_kind == "semantic":
                    cand.features["version_local_sem"] = max(cand.features.get("version_local_sem", 0.0), locality / (1.0 + rank / 10.0))
                elif source_kind == "fts":
                    cand.features["version_local_fts"] = max(cand.features.get("version_local_fts", 0.0), locality / (1.0 + rank / 10.0))
    measure("version_local_expansion", expand_version_local)

    for cand in candidates.values():
        if cand.kind == "version" and cand.base_id in seed_bases:
            cand.features["same_base_version"] = max(cand.features.get("same_base_version", 0.0), 1.0)

    counters["candidate_count_pre_features"] = len(candidates)
    if query_embedding is not None and candidates:
        def batch_candidate_embeddings() -> int:
            counters["candidate_embedding_batches"] += 1
            return _load_candidate_embeddings_batch(kp, chroma_coll, candidates)
        counters["candidate_embedding_fetches"] += measure("candidate_embedding_batch_fetch", batch_candidate_embeddings)

    def decorate_candidates() -> None:
        for cand in candidates.values():
            cand.features["lexical"] = max(
                cand.features.get("lexical", 0.0),
                _term_overlap(query_terms, _candidate_text(cand.summary, cand.tags)),
            )
            cand.features["focus_overlap"] = _term_overlap(set(query_note.get("focus_terms", [])), _candidate_text(cand.summary, cand.tags))
            cand.features["recency"] = _recency_score(cand.updated_at)
            if conv_tag is not None and str(cand.tags.get("conv")) == conv_tag:
                cand.features["same_conv"] = 1.0
            if target_sessions and cand.base_id in target_sessions:
                cand.features["target_session"] = 1.0
            anchor_names = set(query_note.get("anchors", []))
            if anchor_names:
                speaker = str(cand.tags.get("speaker") or "")
                speaker_a = str(cand.tags.get("speaker_a") or "")
                speaker_b = str(cand.tags.get("speaker_b") or "")
                matched_anchors: list[str] = []
                if cand.id in anchor_names:
                    cand.features["anchor_exact"] = 1.0
                    matched_anchors.append(cand.id)
                if speaker in anchor_names:
                    cand.features["anchor_speaker"] = 1.0
                    matched_anchors.append(speaker)
                if speaker_a in anchor_names or speaker_b in anchor_names:
                    cand.features["anchor_participant"] = 1.0
                    if speaker_a in anchor_names:
                        matched_anchors.append(speaker_a)
                    if speaker_b in anchor_names:
                        matched_anchors.append(speaker_b)
                if any(re.search(rf"\b{re.escape(name.casefold())}\b", _candidate_text(cand.summary, cand.tags).casefold()) for name in anchor_names):
                    cand.features["anchor_mention"] = 1.0
                    for name in anchor_names:
                        if re.search(rf"\b{re.escape(name.casefold())}\b", _candidate_text(cand.summary, cand.tags).casefold()):
                            matched_anchors.append(name)
                if matched_anchors:
                    cand.hints["anchor_names"] = sorted(set(matched_anchors))
            kind = query_note.get("kind")
            if kind == "temporal" and cand.tags.get("date"):
                cand.features["question_temporal"] = 1.0
            if kind == "identity":
                text = _candidate_text(cand.summary, cand.tags).casefold()
                if cand.id in anchor_names or "factsheet:" in text or "identity" in text:
                    cand.features["question_identity"] = 1.0
            if query_embedding is not None:
                cand.features["semantic"] = max(
                    cand.features.get("semantic_seed", 0.0),
                    _cosine(query_embedding, cand.embedding),
                )
    measure("feature_decoration", decorate_candidates)

    probe_signals = enrich_find_output({
        "results": [
            {"id": row["id"], "summary": "", "tags": {}, "score": row["score"]}
            for row in semantic_rows[:10]
        ],
        "count": len(semantic_rows),
    })
    toward_ctx: dict[str, Any] = {}
    if probe.toward_id:
        toward_record = measure("toward_record_fetch", lambda: _fetch_candidate_record(kp, probe.toward_id))
        toward_ctx["toward_id"] = probe.toward_id
        toward_ctx["toward_base_id"] = _base_id(probe.toward_id)
        if toward_record is not None:
            toward_summary, toward_tags, _toward_updated = toward_record
            toward_ctx["toward_summary"] = toward_summary
            toward_ctx["toward_tags"] = toward_tags
        toward_embedding = None
        if query_embedding is not None:
            try:
                counters["toward_embedding_fetches"] += 1
                toward_embedding = measure("toward_embedding_fetch", lambda: store.get_embedding(chroma_coll, probe.toward_id))
            except Exception:
                toward_embedding = None
        toward_ctx["toward_embedding"] = toward_embedding

        toward_base = toward_ctx.get("toward_base_id")
        toward_version = _parse_version_id(probe.toward_id)
        def decorate_toward() -> None:
            for cand in candidates.values():
                if cand.id == probe.toward_id:
                    cand.features["toward_exact"] = 1.0
                if toward_base and cand.base_id == toward_base:
                    cand.features["toward_base"] = 1.0
                if toward_embedding is not None:
                    if cand.embedding is None:
                        counters["toward_embedding_fetches"] += 1
                        loaded = _load_candidate_embeddings_batch(kp, chroma_coll, candidates, ids=[cand.id])
                        counters["candidate_embedding_fetches"] += loaded
                        if loaded:
                            counters["candidate_embedding_batches"] += 1
                    cand.features["toward_semantic"] = _cosine(cand.embedding, toward_embedding)
                if toward_version is not None and cand.kind == "version":
                    parsed = _parse_version_id(cand.id)
                    if parsed is not None and parsed[0] == toward_version[0]:
                        cand.features["toward_version_local"] = 1.0 / (1.0 + abs(parsed[1] - toward_version[1]))
        measure("toward_decoration", decorate_toward)

    timings_ms["total"] = round((time.perf_counter() - started_at) * 1000, 2)
    return candidates, semantic_rows, fts_rows_json, {
        "query_embedding": query_embedding,
        "seed_bases": seed_bases,
        "mode": probe.mode,
        "toward": toward_ctx,
        "signals": {
            "margin": probe_signals.get("margin"),
            "entropy": probe_signals.get("entropy"),
        },
        "conv": conv_tag,
        "query_note": query_note,
        "timings_ms": timings_ms,
        "counters": counters,
    }


def run_reconstruction(
    candidates: dict[str, Candidate],
    probe: Probe,
    *,
    weights: dict[str, float],
    support_limit: int,
    min_effective: float,
    redundancy_weight: float,
    same_base_penalty: float,
    residual_threshold: float,
    new_anchor_bonus: float,
    new_base_bonus: float,
) -> dict[str, Any]:
    query_terms = _token_set(probe.query)
    selected: list[str] = []
    selected_bases: set[str] = set()
    selected_embeddings: list[list[float]] = []
    covered_terms: set[str] = set()
    covered_anchors: set[str] = set()
    steps: list[dict[str, Any]] = []

    def effective_score(cand: Candidate) -> tuple[float, float, float]:
        raw = _weighted_score(cand.features, weights)
        redundancy = 0.0
        if cand.embedding and selected_embeddings:
            redundancy = max(_cosine(cand.embedding, emb) for emb in selected_embeddings)
        if cand.base_id in selected_bases:
            redundancy = max(redundancy, same_base_penalty)
        coverage_bonus = 0.0
        if probe.mode == "around":
            cand_anchors = set(str(x) for x in cand.hints.get("anchor_names", []) if x)
            unseen_anchors = cand_anchors - covered_anchors
            if unseen_anchors:
                coverage_bonus += new_anchor_bonus * len(unseen_anchors)
            if cand.base_id not in selected_bases:
                coverage_bonus += new_base_bonus
        effective = raw * max(0.0, 1.0 - redundancy_weight * redundancy) + coverage_bonus
        return raw, redundancy, effective

    for step_num in range(1, support_limit + 1):
        best_id: str | None = None
        best_raw = 0.0
        best_redundancy = 0.0
        best_effective = 0.0
        for cand in candidates.values():
            if cand.id in selected:
                continue
            raw, redundancy, effective = effective_score(cand)
            if effective > best_effective:
                best_id = cand.id
                best_raw = raw
                best_redundancy = redundancy
                best_effective = effective

        if best_id is None or best_effective < min_effective:
            break

        cand = candidates[best_id]
        selected.append(cand.id)
        selected_bases.add(cand.base_id)
        if cand.embedding:
            selected_embeddings.append(cand.embedding)
        covered_terms |= _token_set(_candidate_text(cand.summary, cand.tags))
        covered_anchors |= {str(x) for x in cand.hints.get("anchor_names", []) if x}

        remaining_raws: list[float] = []
        for other in candidates.values():
            if other.id in selected:
                continue
            raw, _redundancy, _effective = effective_score(other)
            remaining_raws.append(raw)
        channel_residual = max(remaining_raws, default=0.0)
        uncovered_terms = sorted(query_terms - covered_terms)

        steps.append({
            "step": step_num,
            "selected_id": cand.id,
            "raw_score": round(best_raw, 4),
            "redundancy": round(best_redundancy, 4),
            "effective_score": round(best_effective, 4),
            "covered_anchors": sorted(covered_anchors),
            "channel_residual": round(channel_residual, 4),
            "coverage_residual": len(uncovered_terms),
            "uncovered_terms": uncovered_terms[:8],
        })

        if channel_residual < residual_threshold and not uncovered_terms:
            break

    metrics = _support_metrics(selected, probe.target_ids)
    return {
        "support_ids": selected,
        "metrics": metrics,
        "steps": steps,
    }


def _toward_feature_values(
    cand: Candidate,
    *,
    toward_id: str,
    toward_base_id: str,
    toward_embedding: list[float] | None,
    toward_version: tuple[str, int] | None,
) -> dict[str, float]:
    values: dict[str, float] = {}
    if cand.id == toward_id:
        values["toward_exact"] = 1.0
    if cand.base_id == toward_base_id:
        values["toward_base"] = 1.0
    if toward_embedding is not None and cand.embedding is not None:
        values["toward_semantic"] = _cosine(cand.embedding, toward_embedding)
    if toward_version is not None and cand.kind == "version":
        parsed = _parse_version_id(cand.id)
        if parsed is not None and parsed[0] == toward_version[0]:
            values["toward_version_local"] = 1.0 / (1.0 + abs(parsed[1] - toward_version[1]))
    return values


def _candidate_with_extra_features(cand: Candidate, extra: dict[str, float]) -> dict[str, float]:
    if not extra:
        return cand.features
    merged = dict(cand.features)
    merged.update(extra)
    return merged


def _propose_toward_handles(
    candidates: dict[str, Candidate],
    probe: Probe,
    *,
    query_note: dict[str, Any] | None,
    weights: dict[str, float],
    max_handles: int,
    max_base_handles: int,
) -> list[str]:
    """Choose a few directional handles for decomposing a concern.

    For `around`, these are candidate subproblems whose local supports
    may need to be merged coverage-first.

    For `towards`, the explicit `probe.toward_id` is the strongest
    available directional handle and should be tried first. Additional
    handles are still useful in the spike because they test whether a
    few nearby directional variants beat a single fixed path.

    The first pass keeps this intentionally simple:
    - explicit toward target first when present
    - explicit anchor/supernode notes next
    - then a few high-scoring local representatives
    """
    query_note = query_note or {}
    handles: list[str] = []
    seen: set[str] = set()

    if probe.toward_id and probe.toward_id in candidates:
        handles.append(probe.toward_id)
        seen.add(probe.toward_id)
        if len(handles) >= max_handles:
            return handles

    for anchor in query_note.get("anchors", []):
        if anchor in candidates and anchor not in seen:
            handles.append(str(anchor))
            seen.add(str(anchor))
            if len(handles) >= max_handles:
                return handles

    ranked = sorted(
        candidates.values(),
        key=lambda cand: (
            _candidate_raw_score(cand, weights)
            + 0.4 * cand.features.get("anchor_edge", 0.0)
            + 0.2 * cand.features.get("anchor_exact", 0.0)
            + 0.15 * cand.features.get("anchor_speaker", 0.0)
        ),
        reverse=True,
    )
    base_count = 0
    for cand in ranked:
        if cand.id in seen:
            continue
        if cand.kind not in {"document", "version"}:
            continue
        handles.append(cand.id)
        seen.add(cand.id)
        base_count += 1
        if len(handles) >= max_handles or base_count >= max_base_handles:
            break
    return handles


def _run_single_towards_path(
    candidates: dict[str, Candidate],
    probe: Probe,
    *,
    weights: dict[str, float],
    toward_id: str,
    support_limit: int,
    min_effective: float,
    redundancy_weight: float,
    same_base_penalty: float,
    residual_threshold: float,
) -> dict[str, Any]:
    toward_cand = candidates.get(toward_id)
    if toward_cand is None:
        return {"support_ids": [], "steps": [], "toward_id": toward_id}

    toward_embedding = toward_cand.embedding
    toward_base_id = toward_cand.base_id
    toward_version = _parse_version_id(toward_id)

    selected: list[str] = []
    selected_bases: set[str] = set()
    selected_embeddings: list[list[float]] = []
    steps: list[dict[str, Any]] = []

    def effective_score(cand: Candidate) -> tuple[float, float, float]:
        features = _candidate_with_extra_features(
            cand,
            _toward_feature_values(
                cand,
                toward_id=toward_id,
                toward_base_id=toward_base_id,
                toward_embedding=toward_embedding,
                toward_version=toward_version,
            ),
        )
        raw = _weighted_score(features, weights)
        redundancy = 0.0
        if cand.embedding and selected_embeddings:
            redundancy = max(_cosine(cand.embedding, emb) for emb in selected_embeddings)
        if cand.base_id in selected_bases:
            redundancy = max(redundancy, same_base_penalty)
        effective = raw * max(0.0, 1.0 - redundancy_weight * redundancy)
        return raw, redundancy, effective

    for step_num in range(1, support_limit + 1):
        best_id: str | None = None
        best_raw = 0.0
        best_redundancy = 0.0
        best_effective = 0.0
        for cand in candidates.values():
            if cand.id in selected:
                continue
            raw, redundancy, effective = effective_score(cand)
            if effective > best_effective:
                best_id = cand.id
                best_raw = raw
                best_redundancy = redundancy
                best_effective = effective

        if best_id is None or best_effective < min_effective:
            break

        cand = candidates[best_id]
        selected.append(cand.id)
        selected_bases.add(cand.base_id)
        if cand.embedding:
            selected_embeddings.append(cand.embedding)

        remaining_raws: list[float] = []
        for other in candidates.values():
            if other.id in selected:
                continue
            raw, _redundancy, _effective = effective_score(other)
            remaining_raws.append(raw)
        channel_residual = max(remaining_raws, default=0.0)
        steps.append({
            "step": step_num,
            "selected_id": cand.id,
            "raw_score": round(best_raw, 4),
            "redundancy": round(best_redundancy, 4),
            "effective_score": round(best_effective, 4),
            "channel_residual": round(channel_residual, 4),
        })
        if channel_residual < residual_threshold:
            break

    return {
        "toward_id": toward_id,
        "support_ids": selected,
        "steps": steps,
    }


def run_multi_towards_reconstruction(
    candidates: dict[str, Candidate],
    probe: Probe,
    *,
    weights: dict[str, float],
    query_note: dict[str, Any] | None,
    support_limit: int,
    max_handles: int,
    max_base_handles: int,
    per_handle_limit: int,
    min_effective: float,
    redundancy_weight: float,
    same_base_penalty: float,
    residual_threshold: float,
) -> dict[str, Any]:
    """Approximate `around` by several directional recoveries plus merge.

    The question here is whether local-neighborhood recovery can often be
    decomposed into a few directional subproblems:
    propose anchor handles, recover toward each, then merge supports
    coverage-first across handles.
    """
    handles = _propose_toward_handles(
        candidates,
        probe,
        query_note=query_note,
        weights=weights,
        max_handles=max_handles,
        max_base_handles=max_base_handles,
    )
    handle_runs: list[dict[str, Any]] = []
    for handle_id in handles:
        handle_runs.append(_run_single_towards_path(
            candidates,
            probe,
            weights=weights,
            toward_id=handle_id,
            support_limit=per_handle_limit,
            min_effective=min_effective,
            redundancy_weight=redundancy_weight,
            same_base_penalty=same_base_penalty,
            residual_threshold=residual_threshold,
        ))

    selected: list[str] = []
    selected_set: set[str] = set()
    selected_bases: set[str] = set()
    merge_steps: list[dict[str, Any]] = []
    for rank in range(per_handle_limit):
        progress = False
        for run in handle_runs:
            if len(selected) >= support_limit:
                break
            support_ids = run.get("support_ids", [])
            if rank >= len(support_ids):
                continue
            cand_id = support_ids[rank]
            if cand_id in selected_set:
                continue
            cand = candidates.get(cand_id)
            if cand is None:
                continue
            selected.append(cand_id)
            selected_set.add(cand_id)
            selected_bases.add(cand.base_id)
            merge_steps.append({
                "step": len(merge_steps) + 1,
                "toward_id": run["toward_id"],
                "rank": rank + 1,
                "selected_id": cand_id,
                "base_id": cand.base_id,
            })
            progress = True
        if len(selected) >= support_limit or not progress:
            break

    metrics = _support_metrics(selected, probe.target_ids)
    return {
        "support_ids": selected,
        "metrics": metrics,
        "handle_ids": handles,
        "handle_runs": handle_runs,
        "steps": merge_steps,
    }


def build_group_candidates(
    candidates: dict[str, Candidate],
    probe: Probe,
    *,
    weights: dict[str, float],
    query_note: dict[str, Any] | None,
    emit_limit: int,
) -> list[GroupCandidate]:
    """Construct generic local support bundles for grouped pursuit.

    This spike deliberately keeps the group vocabulary small and generic.
    The point is not to discover the final abstraction, but to test
    whether `around` improves when selection happens over local bundles
    instead of over isolated notes.
    """
    query_note = query_note or {}
    focus_vocab = set(query_note.get("focus_terms", [])) or (_token_set(probe.query) - STOPWORDS)
    conv_scope = str(query_note.get("conv")) if query_note.get("conv") is not None else None
    raw_scores = {
        cand_id: _candidate_raw_score(cand, weights)
        for cand_id, cand in candidates.items()
    }
    by_base: dict[str, list[Candidate]] = {}
    for cand in candidates.values():
        by_base.setdefault(cand.base_id, []).append(cand)

    groups: dict[str, GroupCandidate] = {}
    seen_signatures: set[tuple[str, ...]] = set()

    def register_group(kind: str, group_id: str, members: list[Candidate], metadata: dict[str, Any] | None = None) -> None:
        if not members:
            return
        if conv_scope is not None:
            members = [cand for cand in members if _candidate_conv(cand) == conv_scope]
            if not members:
                return
        members = sorted(
            members,
            key=lambda cand: (raw_scores.get(cand.id, 0.0), cand.features.get("semantic", 0.0)),
            reverse=True,
        )
        emit_ids = [cand.id for cand in members[:emit_limit]]
        signature = tuple(sorted(emit_ids))
        if not emit_ids or signature in seen_signatures:
            return
        seen_signatures.add(signature)

        anchor_names: set[str] = set()
        focus_terms: set[str] = set()
        base_ids = {cand.base_id for cand in members}
        has_temporal = False
        for cand in members:
            anchor_names |= {str(x) for x in cand.hints.get("anchor_names", []) if x}
            if cand.tags.get("date") or cand.features.get("question_temporal", 0.0) > 0.0:
                has_temporal = True
            cand_terms = _token_set(_candidate_text(cand.summary, cand.tags))
            focus_terms |= (cand_terms & focus_vocab)

        groups[group_id] = GroupCandidate(
            group_id=group_id,
            kind=kind,
            member_ids=[cand.id for cand in members],
            emit_ids=emit_ids,
            base_ids=base_ids,
            anchor_names=anchor_names,
            focus_terms=focus_terms,
            has_temporal=has_temporal,
            score_measurement=_group_measurement_score(emit_ids, raw_scores),
            metadata=metadata or {},
        )

    # Base-local groups are the generic session/lineage neighborhood: all
    # candidates derived from one note lineage share a local explanatory
    # region even if the exact support later comes from versions or parts.
    for base_id, members in by_base.items():
        register_group("base-local", f"base:{base_id}", members, {"base_id": base_id})

        version_members = [cand for cand in members if cand.kind == "version"]
        if len(version_members) >= 2:
            register_group("lineage-local", f"lineage:{base_id}", version_members, {"base_id": base_id})

        part_members = [cand for cand in members if cand.kind == "part"]
        if part_members:
            base_doc = next((cand for cand in members if cand.kind == "document" and cand.id == base_id), None)
            window_members = list(part_members)
            if base_doc is not None:
                window_members.append(base_doc)
            register_group("part-window", f"parts:{base_id}", window_members, {"base_id": base_id})

    # Edge-local groups are generic neighborhoods around supernodes or
    # anchor-bearing notes. Speaker/person neighborhoods are just one
    # instance of this generic edge-local bundle.
    anchor_names = sorted(set(query_note.get("anchors", [])))
    for anchor in anchor_names:
        members = [
            cand for cand in candidates.values()
            if (
                (cand.id == anchor or anchor in {str(x) for x in cand.hints.get("anchor_names", []) if x})
                and (conv_scope is None or _candidate_conv(cand) == conv_scope)
            )
        ]
        if members:
            register_group("edge-local", f"edge:{anchor}", members, {"anchor": anchor})

    return list(groups.values())


def build_group_demands(
    candidates: dict[str, Candidate],
    groups: list[GroupCandidate],
    probe: Probe,
    *,
    weights: dict[str, float],
    query_note: dict[str, Any] | None,
    max_focus_terms: int,
    max_region_bases: int,
    focus_weight: float,
    anchor_weight: float,
    temporal_weight: float,
    region_weight: float,
) -> list[GroupDemand]:
    """Construct the demand side of the grouped reconstruction problem.

    This is the DS3-style shift for the spike. We stop asking only
    "which group looks best?" and instead ask "which groups explain the
    few demands implied by the concern?" The groups are the candidate
    representatives; the demands are the things that need explanation.
    """
    query_note = query_note or {}
    focus_terms = list(query_note.get("focus_terms", []))
    if not focus_terms:
        focus_terms = [term for term in _tokenize(probe.query) if term not in STOPWORDS]
    focus_terms = focus_terms[:max_focus_terms]

    anchor_names = [str(x) for x in query_note.get("anchors", []) if x]
    question_kind = str(query_note.get("kind") or "")
    conv_scope = str(query_note.get("conv")) if query_note.get("conv") is not None else None

    focus_group_counts = {
        term: sum(1 for group in groups if term in group.focus_terms)
        for term in focus_terms
    }
    anchor_group_counts = {
        anchor: sum(
            1
            for group in groups
            if anchor in group.anchor_names or group.metadata.get("anchor") == anchor
        )
        for anchor in anchor_names
    }

    raw_scores = {
        cand_id: _candidate_raw_score(cand, weights)
        for cand_id, cand in candidates.items()
    }
    base_mass: dict[str, float] = {}
    for cand in candidates.values():
        if conv_scope is not None and _candidate_conv(cand) != conv_scope:
            continue
        base_mass[cand.base_id] = max(base_mass.get(cand.base_id, 0.0), raw_scores.get(cand.id, 0.0))
    region_bases = [
        base_id
        for base_id, _score in sorted(base_mass.items(), key=lambda item: item[1], reverse=True)[:max_region_bases]
    ]

    demands: list[GroupDemand] = []
    for term in focus_terms:
        support_count = max(focus_group_counts.get(term, 0), 1)
        rarity_boost = 1.0 + (1.0 / support_count)
        demands.append(GroupDemand(
            demand_id=f"facet:{term}",
            kind="facet",
            label=term,
            weight=focus_weight * rarity_boost,
            metadata={"support_count": support_count},
        ))
    for anchor in anchor_names:
        support_count = max(anchor_group_counts.get(anchor, 0), 1)
        rarity_boost = 1.0 + (1.0 / support_count)
        demands.append(GroupDemand(
            demand_id=f"anchor:{anchor}",
            kind="anchor",
            label=anchor,
            weight=anchor_weight * rarity_boost,
            metadata={"support_count": support_count},
        ))
    if question_kind == "temporal":
        demands.append(GroupDemand(
            demand_id="temporal:dated-support",
            kind="temporal",
            label="dated-support",
            weight=temporal_weight,
        ))
    for base_id in region_bases:
        demands.append(GroupDemand(
            demand_id=f"region:{base_id}",
            kind="region",
            label=base_id,
            weight=region_weight,
            metadata={"base_id": base_id},
        ))
    return demands


def _group_demand_affinity(group: GroupCandidate, demand: GroupDemand) -> float:
    """How well does a group explain one demand?

    The affinity is deliberately simple and bounded in [0, 1]. It is the
    spike's stand-in for the DS3 dissimilarity matrix: cost is 1-affinity.
    """
    if demand.kind == "facet":
        return 1.0 if demand.label in group.focus_terms else 0.0
    if demand.kind == "anchor":
        if demand.label in group.anchor_names or group.metadata.get("anchor") == demand.label:
            return 1.0
        return 0.0
    if demand.kind == "temporal":
        return 1.0 if group.has_temporal else 0.0
    if demand.kind == "region":
        return 1.0 if demand.label in group.base_ids else 0.0
    return 0.0


def run_grouped_reconstruction(
    candidates: dict[str, Candidate],
    probe: Probe,
    *,
    weights: dict[str, float],
    query_note: dict[str, Any] | None,
    support_limit: int,
    emit_limit: int,
    min_effective: float,
    max_focus_demands: int,
    max_region_demands: int,
    focus_demand_weight: float,
    anchor_demand_weight: float,
    temporal_demand_weight: float,
    region_demand_weight: float,
    measurement_weight: float,
    overlap_penalty: float,
    size_penalty: float,
    residual_threshold: float,
) -> dict[str, Any]:
    """Grouped pursuit for `around` probes.

    Flat greedy pursuit works well for directional `towards` recovery,
    but `around` often needs several local bundles that jointly span the
    concern. This grouped loop selects neighborhoods, updates a grouped
    residual vector, and then emits only a few members from each group.
    """
    groups = build_group_candidates(
        candidates,
        probe,
        weights=weights,
        query_note=query_note,
        emit_limit=emit_limit,
    )
    demands = build_group_demands(
        candidates,
        groups,
        probe,
        weights=weights,
        query_note=query_note,
        max_focus_terms=max_focus_demands,
        max_region_bases=max_region_demands,
        focus_weight=focus_demand_weight,
        anchor_weight=anchor_demand_weight,
        temporal_weight=temporal_demand_weight,
        region_weight=region_demand_weight,
    )
    selected_group_ids: list[str] = []
    selected_ids: list[str] = []
    selected_id_set: set[str] = set()
    selected_bases: set[str] = set()
    steps: list[dict[str, Any]] = []
    demand_coverage = {demand.demand_id: 0.0 for demand in demands}
    demand_affinity = {
        group.group_id: {
            demand.demand_id: _group_demand_affinity(group, demand)
            for demand in demands
        }
        for group in groups
    }
    group_measurements = _normalize_scores({
        group.group_id: group.score_measurement
        for group in groups
    })

    def residual_cost() -> float:
        return sum(
            demand.weight * (1.0 - demand_coverage.get(demand.demand_id, 0.0))
            for demand in demands
        )

    def group_effective_score(group: GroupCandidate) -> tuple[float, dict[str, Any]]:
        emit_set = set(group.emit_ids)
        selected_overlap = len(emit_set & selected_id_set)
        denom = max(len(emit_set | selected_id_set), 1)
        jaccard_overlap = selected_overlap / denom if selected_id_set else 0.0
        base_overlap = len(group.base_ids & selected_bases) / max(len(group.base_ids), 1)
        overlap = max(jaccard_overlap, base_overlap)
        assignment_gain = 0.0
        explained_demands: list[dict[str, Any]] = []
        for demand in demands:
            affinity = demand_affinity[group.group_id][demand.demand_id]
            if affinity <= 0.0:
                continue
            prev = demand_coverage.get(demand.demand_id, 0.0)
            gain = max(affinity - prev, 0.0) * demand.weight
            if gain <= 0.0:
                continue
            assignment_gain += gain
            explained_demands.append({
                "demand_id": demand.demand_id,
                "kind": demand.kind,
                "label": demand.label,
                "gain": round(gain, 4),
                "affinity": round(affinity, 4),
            })
        measurement_gain = measurement_weight * group_measurements.get(group.group_id, 0.0)
        effective = assignment_gain + measurement_gain - overlap_penalty * overlap - size_penalty * max(len(group.emit_ids) - 1, 0)
        return effective, {
            "assignment_gain": round(assignment_gain, 4),
            "measurement_gain": round(measurement_gain, 4),
            "explained_demands": explained_demands[:8],
            "overlap": round(overlap, 4),
        }

    while len(selected_ids) < support_limit:
        best_group: GroupCandidate | None = None
        best_effective = float("-inf")
        best_diag: dict[str, Any] = {}
        for group in groups:
            if group.group_id in selected_group_ids:
                continue
            if not any(member_id not in selected_id_set for member_id in group.emit_ids):
                continue
            effective, diag = group_effective_score(group)
            if effective > best_effective:
                best_effective = effective
                best_group = group
                best_diag = diag

        if best_group is None or best_effective < min_effective:
            break

        remaining = support_limit - len(selected_ids)
        emitted_now = [member_id for member_id in best_group.emit_ids if member_id not in selected_id_set][:remaining]
        if not emitted_now:
            selected_group_ids.append(best_group.group_id)
            continue

        selected_group_ids.append(best_group.group_id)
        selected_ids.extend(emitted_now)
        selected_id_set.update(emitted_now)
        selected_bases |= best_group.base_ids
        for demand in demands:
            affinity = demand_affinity[best_group.group_id][demand.demand_id]
            if affinity > demand_coverage[demand.demand_id]:
                demand_coverage[demand.demand_id] = affinity

        remaining_measurement = max((group_measurements.get(group.group_id, 0.0) for group in groups if group.group_id not in selected_group_ids), default=0.0)
        remaining_assignment_gain = max(
            (
                group_effective_score(group)[1]["assignment_gain"]
                for group in groups
                if group.group_id not in selected_group_ids
            ),
            default=0.0,
        )
        uncovered = [
            {
                "demand_id": demand.demand_id,
                "kind": demand.kind,
                "label": demand.label,
                "remaining": round(1.0 - demand_coverage.get(demand.demand_id, 0.0), 4),
                "weight": round(demand.weight, 4),
            }
            for demand in demands
            if demand_coverage.get(demand.demand_id, 0.0) < 0.999
        ]
        residual = {
            "remaining_cost": round(residual_cost(), 4),
            "remaining_measurement": round(remaining_measurement, 4),
            "remaining_assignment_gain": round(remaining_assignment_gain, 4),
            "uncovered_demands": uncovered[:12],
        }
        steps.append({
            "step": len(steps) + 1,
            "selected_group": best_group.group_id,
            "group_kind": best_group.kind,
            "effective_score": round(best_effective, 4),
            "measurement_score": round(best_group.score_measurement, 4),
            "emitted_ids": emitted_now,
            "group_members": best_group.member_ids[:8],
            "diagnostics": best_diag,
            "residual": residual,
        })
        if residual["remaining_cost"] < residual_threshold and remaining_assignment_gain < residual_threshold:
            break

    metrics = _support_metrics(selected_ids, probe.target_ids)
    return {
        "support_ids": selected_ids,
        "metrics": metrics,
        "steps": steps,
        "selected_groups": selected_group_ids,
        "group_count": len(selected_group_ids),
        "demands": [
            {
                "demand_id": demand.demand_id,
                "kind": demand.kind,
                "label": demand.label,
                "weight": round(demand.weight, 4),
                "coverage": round(demand_coverage.get(demand.demand_id, 0.0), 4),
            }
            for demand in demands
        ],
    }


def recipe_config(probe: Probe) -> dict[str, Any]:
    weights = {
        "semantic": 1.0,
        "semantic_rank": 0.3,
        "lexical": 0.6,
        "focus_overlap": 0.0,
        "fts_rank": 0.4,
        "fts_bm25": 0.2,
        "edge": 0.45,
        "version": 0.35,
        "version_local": 0.0,
        "version_local_sem": 0.0,
        "version_local_fts": 0.0,
        "same_base_version": 0.0,
        "toward_exact": 0.0,
        "toward_base": 0.0,
        "toward_semantic": 0.0,
        "toward_version_local": 0.0,
        "part": 0.3,
        "recency": 0.1,
        "same_conv": 0.0,
        "target_session": 0.0,
        "anchor_seed": 0.0,
        "anchor_edge": 0.0,
        "anchor_exact": 0.0,
        "anchor_speaker": 0.0,
        "anchor_participant": 0.0,
        "anchor_mention": 0.0,
        "question_temporal": 0.0,
        "question_identity": 0.0,
    }
    cfg = {
        "weights": weights,
        "redundancy_weight": 0.6,
        "same_base_penalty": 0.5,
        "min_effective": 0.12,
        "residual_threshold": 0.18,
        "new_anchor_bonus": 0.0,
        "new_base_bonus": 0.0,
        "group_emit_limit": 2,
        "group_min_effective": 0.2,
        "group_max_focus_demands": 6,
        "group_max_region_demands": 3,
        "group_focus_demand_weight": 0.55,
        "group_anchor_demand_weight": 0.8,
        "group_temporal_demand_weight": 0.6,
        "group_region_demand_weight": 0.25,
        "group_measurement_weight": 0.35,
        "group_overlap_penalty": 0.9,
        "group_size_penalty": 0.05,
        "multi_towards_max_handles": 3,
        "multi_towards_max_base_handles": 2,
        "multi_towards_per_handle_limit": 2,
        "multi_towards_min_effective": 0.08,
        "multi_towards_redundancy_weight": 0.45,
        "multi_towards_same_base_penalty": 0.15,
        "multi_towards_residual_threshold": 0.12,
    }

    if probe.family == "version":
        weights.update({
            "semantic": 1.05,
            "fts_rank": 0.55,
            "version": 0.6,
            "version_local": 0.85,
            "version_local_sem": 0.45,
            "version_local_fts": 0.65,
            "same_base_version": 0.35,
            "toward_exact": 1.1,
            "toward_base": 0.4,
            "toward_semantic": 0.55,
            "toward_version_local": 0.9,
        })
        cfg.update({
            "redundancy_weight": 0.35,
            "same_base_penalty": 0.1,
            "min_effective": 0.08,
            "residual_threshold": 0.12,
        })
    elif probe.family == "part":
        weights.update({
            "part": 0.6,
            "fts_rank": 0.5,
        })
        cfg.update({
            "same_base_penalty": 0.15,
            "redundancy_weight": 0.45,
        })
    elif probe.family == "edge":
        weights.update({
            "edge": 0.8,
            "fts_rank": 0.45,
            "toward_exact": 1.0,
            "toward_base": 0.5,
            "toward_semantic": 0.35,
        })
        cfg.update({
            "same_base_penalty": 0.35,
            "redundancy_weight": 0.55,
        })
    elif probe.family == "qa":
        weights.update({
            "semantic": 1.0,
            "semantic_rank": 0.35,
            "lexical": 0.75,
            "focus_overlap": 0.7,
            "fts_rank": 0.55,
            "fts_bm25": 0.2,
            "same_conv": 0.8,
            "target_session": 0.4,
            "part": 0.2,
            "edge": 0.25,
            "version": 0.3,
            "anchor_seed": 0.25,
            "anchor_edge": 0.45,
            "anchor_exact": 0.35,
            "anchor_speaker": 0.8,
            "anchor_participant": 0.65,
            "anchor_mention": 0.35,
            "question_temporal": 0.4,
            "question_identity": 0.7,
        })
        cfg.update({
            "same_base_penalty": 0.2,
            "redundancy_weight": 0.45,
            "min_effective": 0.08,
            "residual_threshold": 0.15,
            "new_anchor_bonus": 0.0,
            "new_base_bonus": 0.0,
            "group_emit_limit": 2,
            "group_min_effective": 0.4,
            "group_max_focus_demands": 8,
            "group_max_region_demands": 3,
            "group_focus_demand_weight": 0.65,
            "group_anchor_demand_weight": 0.95,
            "group_temporal_demand_weight": 0.7,
            "group_region_demand_weight": 0.3,
            "group_measurement_weight": 0.4,
            "group_overlap_penalty": 1.0,
            "group_size_penalty": 0.06,
        })
        if probe.mode == "towards":
            weights.update({
                "toward_exact": 1.0,
                "toward_base": 0.5,
                "toward_semantic": 0.45,
                "toward_version_local": 0.7,
                "target_session": 0.7,
            })
        else:
            weights.update({
                "same_conv": 0.95,
                "target_session": 0.6,
                "edge": 0.35,
                "part": 0.35,
                "anchor_edge": 0.65,
                "anchor_speaker": 0.95,
                "anchor_participant": 0.8,
                "question_temporal": 0.55,
                "toward_exact": 0.95,
                "toward_base": 0.45,
                "toward_semantic": 0.35,
                "toward_version_local": 0.65,
            })
            cfg.update({
                "new_anchor_bonus": 0.7,
                "new_base_bonus": 0.15,
                "group_emit_limit": 2,
                "group_min_effective": 0.7,
                "group_max_focus_demands": 8,
                "group_max_region_demands": 4,
                "group_focus_demand_weight": 0.85,
                "group_anchor_demand_weight": 1.15,
                "group_temporal_demand_weight": 0.95,
                "group_region_demand_weight": 0.45,
                "group_measurement_weight": 0.45,
                "group_overlap_penalty": 1.1,
                "group_size_penalty": 0.08,
                "multi_towards_max_handles": 4,
                "multi_towards_max_base_handles": 3,
                "multi_towards_per_handle_limit": 2,
                "multi_towards_min_effective": 0.09,
                "multi_towards_redundancy_weight": 0.4,
                "multi_towards_same_base_penalty": 0.12,
                "multi_towards_residual_threshold": 0.1,
            })

    return cfg


def baseline_semantic(semantic_rows: list[dict[str, Any]], top_k: int) -> list[str]:
    return [row["id"] for row in semantic_rows[:top_k]]


def baseline_fts(fts_rows: list[dict[str, Any]], top_k: int) -> list[str]:
    return [row["id"] for row in fts_rows[:top_k]]


def _probe_tags(probe: Probe) -> dict[str, str] | None:
    conv = probe.metadata.get("conv")
    if conv is None:
        return None
    return {"conv": str(conv)}


def baseline_fused(kp: Keeper, probe: Probe, top_k: int) -> tuple[list[str], dict[str, Any], str | None]:
    try:
        results = kp.find(probe.query, limit=top_k, tags=_probe_tags(probe))
        ids = [item.id for item in results]
        stats = enrich_find_output({
            "results": [
                {"id": item.id, "summary": item.summary, "tags": item.tags, "score": item.score}
                for item in results
            ],
            "count": len(results),
        })
        return ids, {"margin": stats.get("margin"), "entropy": stats.get("entropy")}, None
    except Exception as e:
        doc_coll = kp._resolve_doc_collection()
        rows = kp._document_store.query_fts(doc_coll, probe.query, limit=top_k)
        ids = [row[0] for row in rows]
        return ids, {"margin": None, "entropy": None}, str(e)


def baseline_deep(kp: Keeper, probe: Probe, top_k: int) -> tuple[list[str], str | None]:
    try:
        results = kp.find(probe.query, limit=top_k, deep=True, tags=_probe_tags(probe))
        return _deep_support_ids(results), None
    except Exception as e:
        return [], str(e)


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_method: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        for method_name, method in record["methods"].items():
            if "metrics" not in method:
                continue
            bucket = by_method.setdefault(method_name, [])
            bucket.append(method["metrics"] | {"runtime_ms": method["runtime_ms"]})

    summary: list[dict[str, Any]] = []
    for method_name, metrics in sorted(by_method.items()):
        summary.append({
            "method": method_name,
            "n": len(metrics),
            "strict_hit_rate": round(statistics.mean(1.0 if m["strict_hit"] else 0.0 for m in metrics), 4),
            "strict_recall_mean": round(statistics.mean(m["strict_recall"] for m in metrics), 4),
            "base_hit_rate": round(statistics.mean(1.0 if m["base_hit"] else 0.0 for m in metrics), 4),
            "base_recall_mean": round(statistics.mean(m["base_recall"] for m in metrics), 4),
            "support_size_mean": round(statistics.mean(m["support_size"] for m in metrics), 2),
            "runtime_ms_mean": round(statistics.mean(m["runtime_ms"] for m in metrics), 2),
        })
    return summary


def summarize_by_mode(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        mode = record["probe"]["mode"]
        for method_name, method in record["methods"].items():
            if "metrics" not in method:
                continue
            buckets.setdefault((mode, method_name), []).append(
                method["metrics"] | {"runtime_ms": method["runtime_ms"]}
            )
    rows: list[dict[str, Any]] = []
    for (mode, method_name), metrics in sorted(buckets.items()):
        rows.append({
            "mode": mode,
            "method": method_name,
            "n": len(metrics),
            "strict_hit_rate": round(statistics.mean(1.0 if m["strict_hit"] else 0.0 for m in metrics), 4),
            "strict_recall_mean": round(statistics.mean(m["strict_recall"] for m in metrics), 4),
            "base_hit_rate": round(statistics.mean(1.0 if m["base_hit"] else 0.0 for m in metrics), 4),
            "base_recall_mean": round(statistics.mean(m["base_recall"] for m in metrics), 4),
            "support_size_mean": round(statistics.mean(m["support_size"] for m in metrics), 2),
            "runtime_ms_mean": round(statistics.mean(m["runtime_ms"] for m in metrics), 2),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline sparse-reconstruction spike for keep")
    parser.add_argument("--store", type=str, default="~/.keep", help="Keep store path (default: ~/.keep)")
    parser.add_argument("--families", nargs="+", default=["version", "part", "edge"],
                        choices=["version", "part", "edge", "qa"], help="Probe families to run")
    parser.add_argument("--probes-per-family", type=int, default=5, help="Number of probes per family")
    parser.add_argument("--qa-dataset", type=str, default=None,
                        help="Optional LoCoMo-style qa_dataset.json for question probes")
    parser.add_argument("--qa-mode", type=str, default="auto",
                        choices=["auto", "around", "towards"],
                        help="How qa probes should be interpreted (default: auto)")
    parser.add_argument("--qa-categories", nargs="+", default=None,
                        help="Optional subset of QA categories to sample")
    parser.add_argument("--query-note-model", action="store_true", default=False,
                        help="Enable target-free query-note analysis and anchor features")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--semantic-limit", type=int, default=30)
    parser.add_argument("--fts-limit", type=int, default=30)
    parser.add_argument("--seed-limit", type=int, default=8)
    parser.add_argument("--edge-limit", type=int, default=6)
    parser.add_argument("--version-limit", type=int, default=4)
    parser.add_argument("--part-limit", type=int, default=6)
    parser.add_argument("--support-limit", type=int, default=6)
    parser.add_argument("--min-effective", type=float, default=0.12)
    parser.add_argument("--residual-threshold", type=float, default=0.18)
    parser.add_argument("--redundancy-weight", type=float, default=0.6)
    parser.add_argument("--same-base-penalty", type=float, default=0.5)
    parser.add_argument("--multi-towards-max-handles", type=int, default=None,
                        help="Optional override for the number of directional handles to try")
    parser.add_argument("--multi-towards-max-base-handles", type=int, default=None,
                        help="Optional override for the number of distinct base-note handles to try")
    parser.add_argument("--multi-towards-per-handle-limit", type=int, default=None,
                        help="Optional override for notes to recover from each directional handle")
    parser.add_argument("--judge-model", type=str, default=None,
                        help="Optional OpenAI model for judged sufficiency, e.g. gpt-5-nano")
    parser.add_argument("--out", type=str, default=None, help="Optional JSON output path")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    store_path = Path(args.store).expanduser().resolve()
    kp = Keeper(store_path=store_path)
    judge = OpenAIJudge(model=args.judge_model) if args.judge_model else None

    probe_builders = {
        "version": sample_version_probes,
        "part": sample_part_probes,
        "edge": sample_edge_probes,
    }

    probes: list[Probe] = []
    for family in args.families:
        if family == "qa":
            if not args.qa_dataset:
                raise SystemExit("--qa-dataset is required when using family 'qa'")
            probes.extend(sample_qa_probes(
                Path(args.qa_dataset).expanduser().resolve(),
                rng,
                args.probes_per_family,
                mode=args.qa_mode,
                categories=set(args.qa_categories or []),
            ))
            continue
        probes.extend(probe_builders[family](kp, rng, args.probes_per_family))

    if not probes:
        print("No probes sampled.", file=sys.stderr)
        kp.close()
        return

    print(f"Store: {store_path}", file=sys.stderr)
    print(f"Probes: {len(probes)}", file=sys.stderr)

    records: list[dict[str, Any]] = []
    for idx, probe in enumerate(probes, start=1):
        print(f"[{idx}/{len(probes)}] {probe.probe_id}", file=sys.stderr)
        methods: dict[str, Any] = {}

        t0 = time.perf_counter()
        candidates, semantic_rows, fts_rows, probe_ctx = build_candidate_pool(
            kp,
            probe,
            semantic_limit=args.semantic_limit,
            fts_limit=args.fts_limit,
            seed_limit=args.seed_limit,
            edge_limit=args.edge_limit,
            version_limit=args.version_limit,
            part_limit=args.part_limit,
            query_note_model=args.query_note_model,
        )
        methods["candidate_pool"] = {
            "runtime_ms": round((time.perf_counter() - t0) * 1000, 2),
            "size": len(candidates),
            "seed_bases": probe_ctx["seed_bases"],
            "signals": probe_ctx["signals"],
            "query_note": probe_ctx.get("query_note"),
            "timings_ms": probe_ctx.get("timings_ms", {}),
            "counters": probe_ctx.get("counters", {}),
        }

        recipe = recipe_config(probe)
        if args.multi_towards_max_handles is not None:
            recipe["multi_towards_max_handles"] = args.multi_towards_max_handles
        if args.multi_towards_max_base_handles is not None:
            recipe["multi_towards_max_base_handles"] = args.multi_towards_max_base_handles
        if args.multi_towards_per_handle_limit is not None:
            recipe["multi_towards_per_handle_limit"] = args.multi_towards_per_handle_limit
        grouped_bounds: dict[str, Any] | None = None
        if probe.mode == "around":
            # Compute oracle grouped bounds once so we can tell whether
            # later misses come from pool construction, group induction,
            # or representative emission.
            group_candidates = build_group_candidates(
                candidates,
                probe,
                weights=recipe["weights"],
                query_note=probe_ctx.get("query_note"),
                emit_limit=recipe["group_emit_limit"],
            )
            grouped_bounds = _candidate_pool_upper_bounds(
                candidates,
                probe,
                groups=group_candidates,
            )
            methods["candidate_pool"]["group_upper_bounds"] = grouped_bounds["group_upper_bounds"]

        t0 = time.perf_counter()
        reconstruction = run_reconstruction(
            candidates,
            probe,
            weights=recipe["weights"],
            support_limit=args.support_limit,
            min_effective=recipe["min_effective"],
            redundancy_weight=recipe["redundancy_weight"],
            same_base_penalty=recipe["same_base_penalty"],
            residual_threshold=recipe["residual_threshold"],
            new_anchor_bonus=recipe["new_anchor_bonus"],
            new_base_bonus=recipe["new_base_bonus"],
        )
        methods["reconstruct"] = {
            "runtime_ms": round((time.perf_counter() - t0) * 1000, 2),
            "recipe": {
                "weights": recipe["weights"],
                "redundancy_weight": recipe["redundancy_weight"],
                "same_base_penalty": recipe["same_base_penalty"],
                "min_effective": recipe["min_effective"],
                "residual_threshold": recipe["residual_threshold"],
                "new_anchor_bonus": recipe["new_anchor_bonus"],
                "new_base_bonus": recipe["new_base_bonus"],
            },
            **reconstruction,
        }
        judged = _judge_support(judge, probe, methods["reconstruct"]["support_ids"], kp)
        if judged is not None:
            methods["reconstruct"]["judge"] = judged

        if probe.mode in {"around", "towards"}:
            t0 = time.perf_counter()
            multi_towards = run_multi_towards_reconstruction(
                candidates,
                probe,
                weights=recipe["weights"],
                query_note=probe_ctx.get("query_note"),
                support_limit=args.support_limit,
                max_handles=recipe["multi_towards_max_handles"],
                max_base_handles=recipe["multi_towards_max_base_handles"],
                per_handle_limit=recipe["multi_towards_per_handle_limit"],
                min_effective=recipe["multi_towards_min_effective"],
                redundancy_weight=recipe["multi_towards_redundancy_weight"],
                same_base_penalty=recipe["multi_towards_same_base_penalty"],
                residual_threshold=recipe["multi_towards_residual_threshold"],
            )
            methods["reconstruct_multi_towards"] = {
                "runtime_ms": round((time.perf_counter() - t0) * 1000, 2),
                "recipe": {
                    "weights": recipe["weights"],
                    "multi_towards_max_handles": recipe["multi_towards_max_handles"],
                    "multi_towards_max_base_handles": recipe["multi_towards_max_base_handles"],
                    "multi_towards_per_handle_limit": recipe["multi_towards_per_handle_limit"],
                    "multi_towards_min_effective": recipe["multi_towards_min_effective"],
                    "multi_towards_redundancy_weight": recipe["multi_towards_redundancy_weight"],
                    "multi_towards_same_base_penalty": recipe["multi_towards_same_base_penalty"],
                    "multi_towards_residual_threshold": recipe["multi_towards_residual_threshold"],
                },
                **multi_towards,
            }
            judged = _judge_support(judge, probe, methods["reconstruct_multi_towards"]["support_ids"], kp)
            if judged is not None:
                methods["reconstruct_multi_towards"]["judge"] = judged

        if probe.mode == "around":
            # Grouped pursuit is the new spike for local-neighborhood
            # reconstruction. Keep the flat loop as-is and expose grouped
            # as a parallel method so later runs can compare them directly.
            t0 = time.perf_counter()
            grouped = run_grouped_reconstruction(
                candidates,
                probe,
                weights=recipe["weights"],
                query_note=probe_ctx.get("query_note"),
                support_limit=args.support_limit,
                emit_limit=recipe["group_emit_limit"],
                min_effective=recipe["group_min_effective"],
                max_focus_demands=recipe["group_max_focus_demands"],
                max_region_demands=recipe["group_max_region_demands"],
                focus_demand_weight=recipe["group_focus_demand_weight"],
                anchor_demand_weight=recipe["group_anchor_demand_weight"],
                temporal_demand_weight=recipe["group_temporal_demand_weight"],
                region_demand_weight=recipe["group_region_demand_weight"],
                measurement_weight=recipe["group_measurement_weight"],
                overlap_penalty=recipe["group_overlap_penalty"],
                size_penalty=recipe["group_size_penalty"],
                residual_threshold=recipe["residual_threshold"],
            )
            methods["reconstruct_grouped"] = {
                "runtime_ms": round((time.perf_counter() - t0) * 1000, 2),
                "recipe": {
                    "weights": recipe["weights"],
                    "group_emit_limit": recipe["group_emit_limit"],
                    "group_min_effective": recipe["group_min_effective"],
                    "group_max_focus_demands": recipe["group_max_focus_demands"],
                    "group_max_region_demands": recipe["group_max_region_demands"],
                    "group_focus_demand_weight": recipe["group_focus_demand_weight"],
                    "group_anchor_demand_weight": recipe["group_anchor_demand_weight"],
                    "group_temporal_demand_weight": recipe["group_temporal_demand_weight"],
                    "group_region_demand_weight": recipe["group_region_demand_weight"],
                    "group_measurement_weight": recipe["group_measurement_weight"],
                    "group_overlap_penalty": recipe["group_overlap_penalty"],
                    "group_size_penalty": recipe["group_size_penalty"],
                    "residual_threshold": recipe["residual_threshold"],
                },
                **grouped,
            }
            judged = _judge_support(judge, probe, methods["reconstruct_grouped"]["support_ids"], kp)
            if judged is not None:
                methods["reconstruct_grouped"]["judge"] = judged

        t0 = time.perf_counter()
        sem_ids = baseline_semantic(semantic_rows, args.support_limit)
        methods["semantic"] = {
            "runtime_ms": round((time.perf_counter() - t0) * 1000, 2),
            "support_ids": sem_ids,
            "metrics": _support_metrics(sem_ids, probe.target_ids),
        }
        judged = _judge_support(judge, probe, sem_ids, kp)
        if judged is not None:
            methods["semantic"]["judge"] = judged

        t0 = time.perf_counter()
        fts_ids = baseline_fts(fts_rows, args.support_limit)
        methods["fts"] = {
            "runtime_ms": round((time.perf_counter() - t0) * 1000, 2),
            "support_ids": fts_ids,
            "metrics": _support_metrics(fts_ids, probe.target_ids),
        }
        judged = _judge_support(judge, probe, fts_ids, kp)
        if judged is not None:
            methods["fts"]["judge"] = judged

        t0 = time.perf_counter()
        fused_ids, fused_signals, fused_error = baseline_fused(kp, probe, args.support_limit)
        methods["fused"] = {
            "runtime_ms": round((time.perf_counter() - t0) * 1000, 2),
            "support_ids": fused_ids,
            "signals": fused_signals,
            "metrics": _support_metrics(fused_ids, probe.target_ids),
        }
        if fused_error:
            methods["fused"]["error"] = fused_error
        judged = _judge_support(judge, probe, fused_ids, kp)
        if judged is not None:
            methods["fused"]["judge"] = judged

        t0 = time.perf_counter()
        deep_ids, deep_error = baseline_deep(kp, probe, args.support_limit)
        methods["deep"] = {
            "runtime_ms": round((time.perf_counter() - t0) * 1000, 2),
            "support_ids": deep_ids,
            "metrics": _support_metrics(deep_ids, probe.target_ids),
        }
        if deep_error:
            methods["deep"]["error"] = deep_error
        judged = _judge_support(judge, probe, deep_ids, kp)
        if judged is not None:
            methods["deep"]["judge"] = judged

        pool_bounds = _candidate_pool_upper_bounds(
            candidates,
            probe,
            groups=None if grouped_bounds is None else group_candidates,
            deep_ids=deep_ids,
        )
        methods["candidate_pool"]["upper_bounds"] = pool_bounds

        records.append({
            "probe": {
                "family": probe.family,
                "mode": probe.mode,
                "probe_id": probe.probe_id,
                "query": probe.query,
                "target_ids": probe.target_ids,
                "toward_id": probe.toward_id,
                "metadata": probe.metadata,
            },
            "methods": methods,
        })

    summary = summarize(records)
    mode_summary = summarize_by_mode(records)

    print("\nSummary", file=sys.stderr)
    print(f"{'Method':<14} {'Hit':>8} {'Recall':>8} {'BaseHit':>8} {'BaseRec':>8} {'Size':>8} {'ms':>8}", file=sys.stderr)
    print("-" * 70, file=sys.stderr)
    for row in summary:
        print(
            f"{row['method']:<14} "
            f"{row['strict_hit_rate']:8.3f} "
            f"{row['strict_recall_mean']:8.3f} "
            f"{row['base_hit_rate']:8.3f} "
            f"{row['base_recall_mean']:8.3f} "
            f"{row['support_size_mean']:8.2f} "
            f"{row['runtime_ms_mean']:8.2f}",
            file=sys.stderr,
        )

    print("\nBy mode", file=sys.stderr)
    print(f"{'Mode':<10} {'Method':<14} {'Hit':>8} {'Recall':>8} {'BaseHit':>8} {'BaseRec':>8}", file=sys.stderr)
    print("-" * 70, file=sys.stderr)
    for row in mode_summary:
        print(
            f"{row['mode']:<10} "
            f"{row['method']:<14} "
            f"{row['strict_hit_rate']:8.3f} "
            f"{row['strict_recall_mean']:8.3f} "
            f"{row['base_hit_rate']:8.3f} "
            f"{row['base_recall_mean']:8.3f}",
            file=sys.stderr,
        )

    payload = {
        "store": str(store_path),
        "seed": args.seed,
        "qa_dataset": str(Path(args.qa_dataset).expanduser().resolve()) if args.qa_dataset else None,
        "query_note_model": args.query_note_model,
        "summary": summary,
        "mode_summary": mode_summary,
        "records": records,
    }
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\nWrote {out_path}", file=sys.stderr)

    kp.close()


if __name__ == "__main__":
    main()
