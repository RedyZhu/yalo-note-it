"""Format-neutral archive data shared by source adapters and storage."""
from dataclasses import dataclass, field
import datetime as dt
from pathlib import Path
from typing import Any


THREAD_SCHEMA = 'yalo.codex-thread.v1'
MANIFEST_SCHEMA = 'yalo.archive-manifest.v1'


@dataclass(frozen=True)
class ReferenceContent:
    """A validated value that may name an attachment."""

    value: str
    kind: str = 'text'

    @property
    def is_image(self):
        """Compatibility view used by the format-neutral asset scanner."""
        return self.kind == 'image'


@dataclass(frozen=True)
class ReferenceSource:
    """Attachment-bearing content owned by one stable archive record."""

    record_id: str
    content: tuple[ReferenceContent, ...]


@dataclass(frozen=True)
class AssetCopy:
    """A verified local file copy planned before archive persistence."""

    source: Path
    record_id: str
    sha256: str


@dataclass(frozen=True)
class ArchiveRecord:
    """One stable, source-independent record selected for the archive."""

    record_id: str
    kind: str
    data: dict[str, Any]
    group_id: str | None = None


@dataclass(frozen=True)
class ArchivePackage:
    """Complete, source-independent input to durable archive storage."""

    thread_id: str
    session_id: str
    started_at: dt.datetime
    cutoff_id: str
    cutoff_sha256: str
    document: dict[str, Any]
    source: str
    source_info: dict[str, Any]
    turn_count: int
    records: tuple[ArchiveRecord, ...]
    assets: tuple[AssetCopy, ...] = field(default_factory=tuple)
    raw_evidence: bytes | None = None
    items_view: str = 'full'
    complete: bool = True

    @property
    def record_count(self):
        return len(self.records)
