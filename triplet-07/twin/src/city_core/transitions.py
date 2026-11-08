"""Private immutable replay inputs and bounded paged transition storage."""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any

from .snapshots import Context, PrivateSnapshot
from .effect_records import EffectLedger, EffectRequest

CHUNK_SIZE = 256


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def digest(value):
    data = json.dumps(plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ReplayHeader:
    initial_context: Context
    initial_config: Mapping
    config_digest: str
    catalog_digest: str


@dataclass(frozen=True, slots=True)
class StepInput:
    run_id: str
    expected_revision: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class PrivateTransition:
    record_index: int
    context: Context
    transition_id: str
    cause: str
    input: StepInput | EffectRequest
    before_digest: str
    after_digest: str
    effect_id: str | None = None
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class TraceLog:
    chunks: tuple = ()
    count: int = 0

    def append(self, record):
        if not self.chunks or len(self.chunks[-1]) == CHUNK_SIZE:
            chunks = self.chunks + ((record,),)
        else:
            chunks = self.chunks[:-1] + (self.chunks[-1] + (record,),)
        return TraceLog(chunks, self.count + 1)

    def read(self, start, limit):
        stop = min(start + limit, self.count)
        return tuple(self.chunks[i // CHUNK_SIZE][i % CHUNK_SIZE]
                     for i in range(start, stop))


@dataclass(frozen=True, slots=True)
class RunRecord:
    snapshot: PrivateSnapshot
    topology: Any
    header: ReplayHeader
    trace: TraceLog
    ledger: EffectLedger = field(default_factory=EffectLedger)


@dataclass(frozen=True, slots=True)
class PrivatePage:
    header: ReplayHeader
    records: tuple[PrivateTransition, ...]
    next_after_index: int
    at_end: bool
