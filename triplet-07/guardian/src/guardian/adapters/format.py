"""Format adapter / normalizer: WHAT the data looks like.
Any partner payload -> one canonical Observation, or MalformedPayload (never silently guessed)."""
import json
import math
from datetime import datetime, timezone
from typing import Any, Optional

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from pydantic import ValidationError

from ..models import Observation

CANDIDATES = {
    "observation_id": ["observation_id", "obs_id", "id"],
    "device_id": ["device_id", "sensor_id", "sensorId", "deviceId"],
    "sensor_type": ["sensor_type", "sensorType", "type"],
    "zone": ["zone", "area"],
    "value": ["value", "reading", "val"],
    "unit": ["unit", "units"],
    "timestamp": ["timestamp", "ts", "time"],
    "seq": ["seq", "sequence"],
    "tick": ["tick", "sim_tick"],
    "backup_ids": ["backup_ids", "backups"],
}
REQUIRED = ("device_id", "value", "timestamp")


class MalformedPayload(Exception):
    def __init__(self, reason: str, detail: str = "", observation_id: Optional[str] = None,
                 device_id: Optional[str] = None):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail
        self.observation_id, self.device_id = observation_id, device_id


class FormatAdapter:
    def __init__(self, cfg: dict, partner: str = "default"):
        ad = cfg.get("adapters", {})
        default = ad.get("default", {})
        prof = ad.get("partners", {}).get(partner, {})
        self.partner = partner
        self.field_map: dict = prof.get("field_map", {})            # partner name -> canonical name
        self.conversions: dict = prof.get("unit_conversions", default.get("unit_conversions", {}))
        self.type_by_prefix: dict = {**default.get("type_by_prefix", {}), **prof.get("type_by_prefix", {})}
        # "arrival": the partner's own timestamps are not trusted (for example simulated time, or one wall-clock value
        # shared by many ticks). Guardian stamps readings when they arrive and orders them by seq or tick instead.
        self.timestamp_source: str = prof.get("timestamp_source", "payload")

    # ---- 1. format detection -------------------------------------------------
    def detect(self, raw: Any) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise MalformedPayload("UNSUPPORTED_FORMAT", "payload is not valid UTF-8")
        if not isinstance(raw, str):
            raise MalformedPayload("UNSUPPORTED_FORMAT", f"unsupported payload type {type(raw).__name__}")
        text = raw.strip()
        if not text:
            raise MalformedPayload("EMPTY_PAYLOAD", "empty body")
        if text.startswith("<"):
            return self._xml(text)
        try:
            obj = json.loads(text)
        except ValueError:
            raise MalformedPayload("UNSUPPORTED_FORMAT", "payload is neither JSON nor XML")
        if isinstance(obj, str) and obj.lstrip().startswith("<"):      # JSON string holding XML
            return self._xml(obj.strip())
        if isinstance(obj, dict):
            return obj
        raise MalformedPayload("UNSUPPORTED_FORMAT", "JSON root must be an object")

    @staticmethod
    def _xml(text: str) -> dict:
        try:
            root = SafeET.fromstring(text)          # defusedxml: no entities, no external resources
        except DefusedXmlException:
            raise MalformedPayload("XML_ENTITY_FORBIDDEN", "DTD/entity declarations are not accepted")
        except SafeET.ParseError as e:
            raise MalformedPayload("XML_PARSE_ERROR", str(e))
        flat = dict(root.attrib)
        for child in root:
            if child.text is not None and child.text.strip():
                flat[child.tag] = child.text.strip()
        return flat

    # ---- 2. field mapping + 3. normalization ----------------------------------
    def normalize(self, raw: Any) -> Observation:
        data = self.detect(raw)
        mapped: dict = {}
        for src, canon in self.field_map.items():
            if src in data:
                mapped[canon] = data[src]
        for canon, names in CANDIDATES.items():
            if canon in mapped:
                continue
            for n in names:
                if n in data:
                    mapped[canon] = data[n]
                    break
        oid, dev = mapped.get("observation_id"), mapped.get("device_id")
        oid = str(oid) if oid not in (None, "") else None
        dev = str(dev) if dev not in (None, "") else None
        required = tuple(f for f in REQUIRED if not (f == "timestamp" and self.timestamp_source == "arrival"))
        missing = [f for f in required if mapped.get(f) in (None, "")]
        if missing:
            raise MalformedPayload("MISSING_FIELD", "missing required field(s): " + ", ".join(missing), oid, dev)

        raw_value = mapped["value"]
        try:
            if isinstance(raw_value, bool):
                raise ValueError
            value = float(raw_value)
        except (TypeError, ValueError):
            raise MalformedPayload("INVALID_VALUE", f"value {raw_value!r} is not a number", oid, dev)
        if not math.isfinite(value):
            raise MalformedPayload("INVALID_VALUE", "value must be finite", oid, dev)
        ts = datetime.now(timezone.utc) if self.timestamp_source == "arrival" else self._timestamp(mapped["timestamp"], oid, dev)

        stype = mapped.get("sensor_type") or next(
            (t for p, t in self.type_by_prefix.items() if dev.startswith(p)), "unknown")
        unit = str(mapped.get("unit") or "")
        conv = self.conversions.get(stype)
        if conv and conv.get("when_unit") not in (None, unit):       # optional: only convert a given source unit (m -> cm)
            conv = None
        if conv:                                    # units must match across peers, or cross-checks lie
            value *= conv.get("factor", 1)
            unit = conv.get("unit", unit)
        ints = {}
        for name in ("seq", "tick"):
            raw_i = mapped.get(name)
            try:
                ints[name] = int(raw_i) if raw_i not in (None, "") and not isinstance(raw_i, bool) else None
            except (TypeError, ValueError):
                raise MalformedPayload("INVALID_VALUE", f"{name} {raw_i!r} is not an integer", oid, dev)
        seq, tick = ints["seq"], ints["tick"]
        if seq is None and self.timestamp_source == "arrival":
            seq = tick                                  # no seq: the tick is the ordering key
        backups = mapped.get("backup_ids") or []
        if not isinstance(backups, list):
            raise MalformedPayload("INVALID_VALUE", "backup_ids must be a list", oid, dev)
        oid = oid or (f"OBS-{dev}-{seq}" if seq is not None and self.timestamp_source == "arrival"
                      else f"OBS-{dev}-{int(ts.timestamp() * 1000)}")
        try:
            return Observation(observation_id=oid, device_id=dev, sensor_type=stype,
                               zone=str(mapped.get("zone") or "default"), value=value, unit=unit,
                               timestamp=ts, seq=seq, tick=tick, backup_ids=[str(b) for b in backups])
        except ValidationError as e:
            raise MalformedPayload("SCHEMA_ERROR", e.errors()[0]["msg"], oid, dev)

    @staticmethod
    def _timestamp(v: Any, oid, dev) -> datetime:
        try:
            if isinstance(v, (int, float)) and not isinstance(v, bool) or (isinstance(v, str) and v.replace(".", "", 1).isdigit()):
                x = float(v)
                return datetime.fromtimestamp(x / 1000 if x > 1e11 else x, tz=timezone.utc)
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)   # naive time is assumed to be UTC
        except (ValueError, OverflowError, OSError):
            raise MalformedPayload("INVALID_TIMESTAMP", f"cannot read timestamp {v!r}", oid, dev)
