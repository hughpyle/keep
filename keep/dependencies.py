"""Core note-dependency traversal helpers.

This service is the shared boundary for note dependency queries. Callers ask
for dependency relationships in semantic terms; the service owns whether those
answers come from indexed store queries or, in the future, a materialized
dependency tracker.

The current implementation is query-backed over the document store's `edges`,
`version_edges`, `document_parts`, and `document_versions` tables.
"""

from __future__ import annotations

from dataclasses import dataclass

from .document_store import DocumentStore


@dataclass(frozen=True)
class NoteEdgeDependency:
    """One note-to-note dependency edge.

    ``relationship`` is a forward predicate for outbound traversals and an
    inverse predicate for inbound traversals.
    """

    relationship: str
    note_id: str
    created: str


@dataclass(frozen=True)
class VersionedNoteEdgeDependency:
    """One archived-version note-to-note dependency edge."""

    version: int
    relationship: str
    note_id: str
    created: str


@dataclass(frozen=True)
class NoteStructuralDependencies:
    """Structural exported surfaces attached to a note id."""

    note_id: str
    part_numbers: tuple[int, ...]
    version_numbers: tuple[int, ...]


class NoteDependencyService:
    """Resolve note dependencies for one collection."""

    def __init__(self, store: DocumentStore, collection: str):
        self._store = store
        self._collection = collection

    def current_targets(self, source_id: str) -> list[NoteEdgeDependency]:
        """Return current-note targets reached from ``source_id``."""
        return [
            NoteEdgeDependency(
                relationship=predicate,
                note_id=target_id,
                created=created,
            )
            for predicate, target_id, created
            in self._store.get_forward_edges(self._collection, source_id)
        ]

    def current_sources(self, target_id: str) -> list[NoteEdgeDependency]:
        """Return current-note sources that point at ``target_id``."""
        return [
            NoteEdgeDependency(
                relationship=inverse,
                note_id=source_id,
                created=created,
            )
            for inverse, source_id, created
            in self._store.get_inverse_edges(self._collection, target_id)
        ]

    def archived_targets(
        self,
        source_id: str,
        *,
        limit: int = 1000,
    ) -> list[VersionedNoteEdgeDependency]:
        """Return archived-version targets reached from ``source_id``."""
        return [
            VersionedNoteEdgeDependency(
                version=version,
                relationship=predicate,
                note_id=target_id,
                created=created,
            )
            for version, predicate, target_id, created
            in self._store.get_forward_version_edges(
                self._collection, source_id, limit=limit,
            )
        ]

    def archived_sources(
        self,
        target_id: str,
        *,
        limit: int = 200,
    ) -> list[NoteEdgeDependency]:
        """Return note sources whose archived versions point at ``target_id``."""
        return [
            NoteEdgeDependency(
                relationship=inverse,
                note_id=source_id,
                created=created,
            )
            for inverse, source_id, created
            in self._store.get_inverse_version_edges(
                self._collection, target_id, limit=limit,
            )
        ]

    def structural_surfaces(
        self,
        note_id: str,
        *,
        version_limit: int = 10000,
    ) -> NoteStructuralDependencies:
        """Return the structural sidecar surfaces attached to ``note_id``."""
        parts = self._store.list_parts(self._collection, note_id)
        versions = self._store.list_versions(
            self._collection, note_id, limit=version_limit,
        )
        return NoteStructuralDependencies(
            note_id=note_id,
            part_numbers=tuple(part.part_num for part in parts),
            version_numbers=tuple(version.version for version in versions),
        )

    def all_target_ids(
        self,
        source_id: str,
        *,
        include_archived: bool = True,
        version_limit: int = 1000,
    ) -> list[str]:
        """Return deduplicated current+archived targets for ``source_id``."""
        target_ids = [
            dep.note_id
            for dep in self.current_targets(source_id)
        ]
        if include_archived:
            target_ids.extend(
                dep.note_id
                for dep in self.archived_targets(source_id, limit=version_limit)
            )
        return _dedupe_preserve_order(target_ids)

    def all_source_ids(
        self,
        target_id: str,
        *,
        include_archived: bool = True,
        version_limit: int = 200,
    ) -> list[str]:
        """Return deduplicated current+archived sources for ``target_id``."""
        source_ids = [
            dep.note_id
            for dep in self.current_sources(target_id)
        ]
        if include_archived:
            source_ids.extend(
                dep.note_id
                for dep in self.archived_sources(target_id, limit=version_limit)
            )
        return _dedupe_preserve_order(source_ids)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
