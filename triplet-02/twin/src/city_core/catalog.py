"""The provisional accepted catalog, validated before exposing topology."""
from dataclasses import dataclass
import json
from pathlib import Path

from .validation import InvalidInput, identity, shape, version

CATALOG_VERSION = "pa-catalog-0.1-draft"
MODEL_VERSION = "pa-model-0.2-draft"
CONTRACT_VERSION = "pa-internal-0.2-draft"
DATA = Path(__file__).with_name("data")


def baseline_catalog():
    """Fresh composition input; not a partner-facing state API."""
    return json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))


def baseline_config():
    """Fresh private initialization fixture; never supplied to C's read handle."""
    return json.loads((DATA / "initial_config.json").read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class CatalogEntity:
    id: str
    kind: str
    location_id: str


@dataclass(frozen=True, slots=True)
class Relationship:
    source_id: str
    relation: str
    target_id: str


@dataclass(frozen=True, slots=True)
class DeviceBinding:
    id: str
    family: str
    location_id: str
    binding_relation: str
    target_id: str


@dataclass(frozen=True, slots=True)
class Topology:
    catalog_version: str
    entities: tuple[CatalogEntity, ...]
    relationships: tuple[Relationship, ...]
    devices: tuple[DeviceBinding, ...]


def _records(values, cls):
    if type(values) is not list:
        raise InvalidInput()
    keys = cls.__dataclass_fields__
    result = []
    for value in values:
        shape(value, keys)
        for item in value.values():
            identity(item)
        result.append(cls(**value))
    return tuple(result)


def _canonical(value):
    """Input order does not change the same static topology."""
    return {name: sorted(values, key=lambda v: json.dumps(v, sort_keys=True))
            if type(values) is list else values for name, values in value.items()}


def parse_catalog(value):
    shape(value, ("catalog_version", "entities", "relationships", "devices"))
    version(value["catalog_version"], CATALOG_VERSION)
    entities = _records(value["entities"], CatalogEntity)
    relationships = _records(value["relationships"], Relationship)
    devices = _records(value["devices"], DeviceBinding)
    ids = [item.id for item in (*entities, *devices)]
    if len(ids) != len(set(ids)) or len(relationships) != len(set(relationships)):
        raise InvalidInput()
    registry = {item.id: item for item in entities}
    for entity in entities:
        if entity.location_id != "ROOT" and entity.location_id not in registry:
            raise InvalidInput("UNKNOWN_TARGET")
    for relation in relationships:
        if relation.source_id not in registry or relation.target_id not in registry:
            raise InvalidInput("UNKNOWN_TARGET")
    for device in devices:
        if device.target_id not in registry or device.location_id not in registry:
            raise InvalidInput("UNKNOWN_TARGET")
    # No alternate topology is approved under the same version. Exact records
    # also enforce endpoint direction, kind, family, binding and completeness.
    approved = baseline_catalog()
    if _canonical(value) != _canonical(approved):
        raise InvalidInput()
    return Topology(CATALOG_VERSION,
                    _records(approved["entities"], CatalogEntity),
                    _records(approved["relationships"], Relationship),
                    _records(approved["devices"], DeviceBinding))
