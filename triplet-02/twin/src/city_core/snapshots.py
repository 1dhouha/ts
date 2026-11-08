"""Separate immutable capabilities; C's handle retains no physical state."""
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

from .catalog import Topology
from .state import PhysicalState
from .power import PowerState, derive_power
from .water import WaterInputs, derive_water, flood_exposure
from .traffic import derive_traffic
from .access import derive_access
from .stores import PrivateStore, PublicStore
from .validation import InvalidInput, identity

T = TypeVar("T")
MAX_TOKENS = 100000


@dataclass(frozen=True, slots=True)
class Context:
    run_id: str
    tick: int
    sim_time_ms: int
    revision: int
    model_version: str
    catalog_version: str
    contract_version: str


@dataclass(frozen=True, slots=True)
class Failure:
    code: str
    safe_message: str
    retryable: bool
    context: Context | None


@dataclass(frozen=True, slots=True)
class Success(Generic[T]):
    value: T
    ok: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True)
class Rejected:
    error: Failure
    ok: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class PrivateSnapshot:
    context: Context
    config_digest: str
    physical_state: PhysicalState

    @property
    def power(self) -> PowerState:
        """Derived private facts, never writable primary state or C metadata."""
        return derive_power(self.physical_state)

    @property
    def water(self) -> WaterInputs:
        """Current inputs/capacities; completed-step actuals are in water.last_step."""
        return derive_water(self.physical_state)

    @property
    def flood(self):
        """Private mapped water exposure, not road routing or traffic state."""
        return flood_exposure(self.physical_state)

    @property
    def traffic(self):
        """Private current road conditions/capacity, not observed camera reports."""
        return derive_traffic(self.physical_state)

    @property
    def access(self):
        """Private current graph connectivity, separate from hospital power."""
        return derive_access(self.physical_state)


@dataclass(frozen=True, slots=True)
class Metadata:
    context: Context


@dataclass(frozen=True, slots=True)
class EvidenceToken:
    context: Context
    correlation_id: str | None
    token_id: str


def checked_run(run_id, context):
    identity(run_id)
    if context is None:
        raise InvalidInput("NOT_READY")
    if run_id != context.run_id:
        raise InvalidInput("OLD_RUN")


def rejected(code, context):
    return Rejected(Failure(code, "Read request rejected", False, context))


@dataclass(frozen=True, slots=True)
class ObservationReader:
    _snapshot: PrivateStore = field(repr=False)

    def read_private(self, run_id):
        with self._snapshot.lock:
            current = self._snapshot.current
            context = current.snapshot.context if current else None
            try:
                checked_run(run_id, context)
                return Success(current.snapshot)
            except InvalidInput as error:
                return rejected(error.code, context)


@dataclass(frozen=True, slots=True)
class MetadataReader:
    _metadata: PublicStore = field(repr=False)
    _topology: PublicStore = field(repr=False)

    def read_metadata(self):
        with self._metadata.lock:
            current = self._metadata.current
            return Success(current.metadata) if current else rejected("NOT_READY", None)

    def read_topology(self):
        with self._topology.lock:
            current = self._topology.current
            return Success(current.topology) if current else rejected("NOT_READY", None)

    def make_evidence_token(self, run_id, correlation_id=None):
        with self._metadata.lock:
            current = self._metadata.current
            context = current.metadata.context if current else None
            try:
                checked_run(run_id, context)
                if correlation_id is not None:
                    if (type(correlation_id) is not str or not 1 <= len(correlation_id) <= 128
                            or not correlation_id.isascii() or
                            any(ord(c) < 32 or ord(c) == 127 for c in correlation_id)):
                        raise InvalidInput()
                if self._metadata.token_count >= MAX_TOKENS:
                    raise InvalidInput("MODEL_LIMIT")
                sequence = self._metadata.token_count + 1
                token = EvidenceToken(context, correlation_id, f"ev-{sequence:010d}")
                self._metadata.token_count = sequence
                return Success(token)
            except InvalidInput as error:
                return rejected(error.code, context)
