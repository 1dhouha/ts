"""Composition-owned lifecycle authority and narrow live consumer handles."""
from dataclasses import dataclass

from .lifecycle import Lifecycle, DEFAULT
from .effects import Effects
from .snapshots import MetadataReader, ObservationReader, Success
from .stores import store_pair


@dataclass(frozen=True, slots=True)
class CityCore:
    observations: ObservationReader
    metadata: MetadataReader
    lifecycle: Lifecycle
    actuators: Effects
    scenarios: Effects


def create_core():
    """Allocate an uninitialized authority; only its owner gets lifecycle access."""
    private, public = store_pair()
    owner = Lifecycle(private, public)
    return CityCore(ObservationReader(private), MetadataReader(public, public),
                    owner, Effects(owner, "B_ACTUATOR"), Effects(owner, "B_SCENARIO"))


def build_city(run_id, *, initial_config=DEFAULT, catalog=DEFAULT):
    """Phase02-compatible construction, now backed by the live lifecycle."""
    core = create_core()
    result = core.lifecycle.initialize(run_id, initial_config=initial_config, catalog=catalog)
    return Success(core) if result.ok else result
