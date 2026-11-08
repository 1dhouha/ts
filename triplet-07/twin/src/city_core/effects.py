"""Source-bound immediate effect capability; the lifecycle owns the only commit."""
from dataclasses import dataclass, field, replace

from . import lifecycle
from .effect_records import EffectReceipt, parse_request
from .effect_state import apply_state
from .snapshots import Failure, Metadata, PrivateSnapshot, Rejected, Success
from .stores import PublicView
from .transitions import PrivateTransition, digest
from .validation import InvalidInput

MAX_EFFECTS = 100000


@dataclass(frozen=True, slots=True)
class Effects:
    _owner: lifecycle.Lifecycle = field(repr=False)
    _source: str = field(repr=False)

    def apply_effect(self, request):
        with self._owner._private.lock:
            try:
                parsed = parse_request(request, self._source)
                current = self._owner._current(parsed.run_id)
                entry = current.ledger.find(parsed.effect_id)
                if entry is not None:
                    if entry.request != parsed:
                        raise InvalidInput("IDENTITY_CONFLICT")
                    return Success(replace(entry.receipt, duplicate=True))
                self._owner._current(parsed.run_id, parsed.expected_revision)
                if current.ledger.count >= MAX_EFFECTS or current.trace.count >= lifecycle.MAX_RECORDS:
                    raise InvalidInput("MODEL_LIMIT")
                physical = apply_state(current, parsed.effect)
                context = replace(current.snapshot.context, revision=current.snapshot.context.revision + 1)
                sequence = current.ledger.count
                receipt = EffectReceipt(context, parsed.effect_id, sequence, parsed.correlation_id)
                snapshot = PrivateSnapshot(context, current.snapshot.config_digest, physical)
                record = PrivateTransition(current.trace.count, context, f"tr-{context.revision:010d}",
                    "EFFECT", parsed, digest(current.snapshot.physical_state), digest(physical),
                    parsed.effect_id, sequence)
                updated = replace(current, snapshot=snapshot, trace=current.trace.append(record),
                                  ledger=current.ledger.append(parsed, receipt))
                view = PublicView(Metadata(context), current.topology)
                self._owner._private.current = updated
                self._owner._public.current = view
                return Success(receipt)
            except InvalidInput as error:
                current = self._owner._private.current
                context = current.snapshot.context if current else None
                return Rejected(Failure(error.code, "Effect request rejected",
                                        error.code == "STATE_CONFLICT", context))
