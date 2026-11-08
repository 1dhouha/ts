"""A-owned atomic initialize, fixed step, reset and private trace boundary."""
from dataclasses import dataclass, field, replace
import json

from .catalog import (CATALOG_VERSION, CONTRACT_VERSION, MODEL_VERSION,
                      baseline_catalog, baseline_config, parse_catalog)
from .clock import next_context, validate_revision
from .snapshots import (Context, Failure, Metadata, PrivateSnapshot, Rejected, Success)
from .state import parse_config
from .water import advance_water
from .traffic import advance_traffic
from .responders import advance_responders
from .air import advance_air
from .stores import PrivateStore, PublicStore, PublicView
from .transitions import (PrivatePage, PrivateTransition, ReplayHeader, RunRecord,
                          StepInput, TraceLog, digest, freeze)
from .validation import InvalidInput, identity

DEFAULT = object()
MAX_TICKS = 100000
MAX_RECORDS = 200000
MAX_RUNS = 10000


@dataclass(frozen=True, slots=True)
class LifecycleReceipt:
    context: Context
    status: str = "READY"


def prepare(run_id, initial_config, catalog):
    identity(run_id)
    selected = baseline_catalog() if catalog is DEFAULT else catalog
    config = baseline_config() if initial_config is DEFAULT else initial_config
    topology = parse_catalog(selected)
    physical = parse_config(config, topology)
    context = Context(run_id, 0, 0, 0, MODEL_VERSION, CATALOG_VERSION, CONTRACT_VERSION)
    config_hash = digest(config)
    snapshot = PrivateSnapshot(context, config_hash, physical)
    header = ReplayHeader(context, freeze(config), config_hash, digest(topology))
    return RunRecord(snapshot, topology, header, TraceLog())


@dataclass(slots=True)
class Lifecycle:
    _private: PrivateStore = field(repr=False)
    _public: PublicStore = field(repr=False)
    _consumed_runs: frozenset = field(default_factory=frozenset, repr=False)

    def _failure(self, code):
        current = self._private.current
        context = current.snapshot.context if current else None
        return Rejected(Failure(code, "Core request rejected", code == "STATE_CONFLICT", context))

    def _current(self, run_id, expected_revision=None):
        identity(run_id)
        current = self._private.current
        if current is None:
            raise InvalidInput("NOT_READY")
        if run_id != current.snapshot.context.run_id:
            raise InvalidInput("OLD_RUN")
        if expected_revision is not None:
            validate_revision(expected_revision)
            if expected_revision != current.snapshot.context.revision:
                raise InvalidInput("STATE_CONFLICT")
        return current

    def _publish(self, record):
        # All validation/allocation precedes pointer publication under one lock.
        view = PublicView(Metadata(record.snapshot.context), record.topology)
        consumed = self._consumed_runs | {record.snapshot.context.run_id}
        self._private.current = record
        self._public.current = view
        self._public.token_count = 0
        self._consumed_runs = consumed

    def initialize(self, run_id, *, initial_config=DEFAULT, catalog=DEFAULT):
        with self._private.lock:
            try:
                if self._private.current is not None:
                    raise InvalidInput("ALREADY_INITIALIZED")
                record = prepare(run_id, initial_config, catalog)
                self._publish(record)
                return Success(LifecycleReceipt(record.snapshot.context))
            except InvalidInput as error:
                return self._failure(error.code)
            except (OSError, json.JSONDecodeError):
                return self._failure("NOT_READY")

    def advance_one(self, run_id, expected_revision, *, duration_ms=1000):
        with self._private.lock:
            try:
                validate_revision(expected_revision)
                current = self._current(run_id, expected_revision)
                context = next_context(current.snapshot.context, duration_ms)
                if context.tick > MAX_TICKS or current.trace.count >= MAX_RECORDS:
                    raise InvalidInput("MODEL_LIMIT")
                physical = advance_water(current.snapshot.physical_state,
                                         current.header.initial_config["parameters"])
                physical = advance_traffic(physical, current.topology,
                                           current.header.initial_config['parameters'])
                physical = advance_responders(physical, current.topology,
                                              current.header.initial_config['parameters'])
                physical = advance_air(physical, current.header.initial_config['parameters'])
                snapshot = PrivateSnapshot(context, current.snapshot.config_digest, physical)
                before_hash = digest(current.snapshot.physical_state)
                after_hash = digest(physical)
                step = StepInput(run_id, expected_revision, duration_ms)
                record = PrivateTransition(current.trace.count, context,
                    f"tr-{context.revision:010d}", "STEP", step, before_hash, after_hash)
                updated = replace(current, snapshot=snapshot, trace=current.trace.append(record))
                view = PublicView(Metadata(context), current.topology)
                self._private.current = updated
                self._public.current = view
                return Success(snapshot)
            except InvalidInput as error:
                return self._failure(error.code)

    def reset(self, old_run_id, expected_revision, new_run_id, *, initial_config=DEFAULT):
        with self._private.lock:
            try:
                validate_revision(expected_revision)
                self._current(old_run_id, expected_revision)
                identity(new_run_id)
                if new_run_id in self._consumed_runs:
                    raise InvalidInput("RUN_ID_REUSED")
                if len(self._consumed_runs) >= MAX_RUNS:
                    raise InvalidInput("MODEL_LIMIT")
                record = prepare(new_run_id, initial_config, DEFAULT)
                self._publish(record)
                return Success(LifecycleReceipt(record.snapshot.context))
            except InvalidInput as error:
                return self._failure(error.code)
            except (OSError, json.JSONDecodeError):
                return self._failure("NOT_READY")

    def read_private_transitions(self, run_id, after_index, limit):
        with self._private.lock:
            try:
                current = self._current(run_id)
                if (type(after_index) is not int or type(limit) is not int
                        or not 1 <= limit <= 1000):
                    raise InvalidInput()
                if after_index < -1 or (after_index >= current.trace.count and after_index != -1):
                    raise InvalidInput("CURSOR_INVALID")
                records = current.trace.read(after_index + 1, limit)
                cursor = records[-1].record_index if records else after_index
                return Success(PrivatePage(current.header, records, cursor,
                                           cursor + 1 == current.trace.count))
            except InvalidInput as error:
                return self._failure(error.code)
