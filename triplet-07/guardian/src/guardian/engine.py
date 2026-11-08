import statistics
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml

from .eventlog import EventLog
from .models import Observation, Verdict

HARD = {"OUT_OF_RANGE"}


def _merge(base: dict, over: dict) -> dict:
    """Deep merge: dicts merge key by key, everything else (lists, scalars, null) is replaced by the overlay."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Optional[str] = None, profile: Optional[str] = None) -> dict:
    """config.yaml, plus config.<profile>.yaml on top when a profile is chosen (GUARDIAN_PROFILE=twin).
    profile="" means no profile even if the environment names one (scenarios and tests use the base config)."""
    import os
    p = Path(path) if path else Path(__file__).resolve().parents[2] / "config.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    profile = os.getenv("GUARDIAN_PROFILE", "") if profile is None else profile
    if profile:
        if not profile.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"bad profile name {profile!r}")
        over = p.with_name(f"{p.stem}.{profile}.yaml")
        if not over.exists():
            raise FileNotFoundError(f"profile {profile!r} needs {over.name} next to {p.name}")
        cfg = _merge(cfg, yaml.safe_load(over.read_text(encoding="utf-8")) or {})
    if os.getenv("TWIN_URL"):                       # the Twin's address changes from machine to machine: environment wins
        cfg.setdefault("partners", {})["twin_url"] = os.environ["TWIN_URL"].strip().rstrip("/")
    return cfg


@dataclass
class DeviceState:
    sensor_type: str
    zone: str
    trust: float = 1.0
    quarantined: bool = False
    last_ts: Optional[float] = None
    last_seq: Optional[int] = None
    last_value: Optional[float] = None
    history: deque = field(default_factory=lambda: deque(maxlen=20))
    replays_rejected: int = 0
    stale: bool = False
    n_seen: int = 0                                   # accepted readings so far (a new device is on probation as a peer)
    arrivals: deque = field(default_factory=lambda: deque(maxlen=64))   # arrival times, for the flood limiter
    flooding: bool = False
    refused: int = 0                                  # messages refused without judging the device (flood, future timestamp)
    last_tick: Optional[int] = None
    clean: deque = field(default_factory=lambda: deque(maxlen=20))   # recent values that passed every check: the baseline a fake cannot move
    backups: list = field(default_factory=list)       # redundant twin sensors declared by the partner (backup pair check)
    unavailable: bool = False                         # the partner reports the device offline (not an attack)
    drift: float = 0.0                                # smoothed gap between this device and its reference (peers or backup)
    drift_n: int = 0                                  # readings in a row with that gap above the drift tolerance
    ref_hist: deque = field(default_factory=lambda: deque(maxlen=64))   # recent reference values (peer median or backup), to tell a stuck sensor from a calm world


class GuardianEngine:
    def __init__(self, cfg: Optional[dict] = None, on_contain: Optional[Callable[[str, str], None]] = None,
                 eventlog: Optional[EventLog] = None, use_obs_time: bool = False,
                 clock: Optional[Callable[[], datetime]] = None,
                 on_release: Optional[Callable[[str], None]] = None):
        self.cfg = cfg or load_config()
        self.on_contain = on_contain
        self.on_release = on_release
        self.log = eventlog or EventLog()
        self.use_obs_time = use_obs_time  # simulations: stamp verdicts with observation time
        # clock: what "now" means. Live: the wall clock. Simulation without a clock: no future-timestamp check,
        # and the flood limiter counts in observation time. A scenario can pass its own simulated clock.
        self.clock = clock if clock is not None else (None if use_obs_time else (lambda: datetime.now(timezone.utc)))
        self.sec = self.cfg.get("security", {})
        reg = self.sec.get("registry", {})
        self._allowed_ids = set(reg.get("allowed_ids", []))
        self._allowed_prefixes = tuple(reg.get("allowed_prefixes", []))
        self.devices: dict[str, DeviceState] = {}
        self.groups: dict[tuple, set] = {}
        self.zone_count: Counter = Counter()
        self.verdicts: "OrderedDict[str, Verdict]" = OrderedDict()
        self._n = 0

    # ---- helpers -------------------------------------------------------
    def _tcfg(self, sensor_type: str) -> dict:
        return self.cfg["sensor_types"].get(sensor_type, self.cfg["default"])

    def _store(self, v: Verdict) -> None:
        """Keep verdicts for lookup, but bounded: an attacker must not be able to fill our memory."""
        self.verdicts[v.observation_id] = v
        cap = self.sec.get("max_verdicts", 50000)
        while len(self.verdicts) > cap:
            self.verdicts.popitem(last=False)

    def _now(self, obs: Observation) -> float:
        if self.clock is not None:
            return self.clock().timestamp()
        return obs.timestamp.timestamp()          # simulation: time is carried by the observations

    def _registry_problem(self, obs: Observation) -> Optional[str]:
        """Is this unknown device allowed to join? Stops fake-device injection (a Sybil attack on the peer check)."""
        reg = self.sec.get("registry", {})
        mode = reg.get("mode", "open")
        if len(self.devices) >= reg.get("max_devices", 1000):
            return "device limit reached"
        if self.zone_count[obs.zone] >= reg.get("max_devices_per_zone", 50):
            return f"zone {obs.zone!r} is full"
        if mode == "strict" and obs.device_id not in self._allowed_ids:
            return "device is not in security.registry.allowed_ids"
        if mode == "prefix" and not obs.device_id.startswith(self._allowed_prefixes):
            return "device id prefix is not in security.registry.allowed_prefixes"
        return None

    def _refuse(self, obs: Observation, code: str, detail: str, st: Optional[DeviceState] = None) -> Verdict:
        """Refuse a message WITHOUT judging the device it claims to come from: the sender of a flood, a replay or a
        forged timestamp can be anyone, so the claimed device's trust and ordering state are left untouched."""
        self._n += 1
        v = Verdict(
            verdict_id=f"VRD-{self._n:06d}", observation_id=obs.observation_id, device_id=obs.device_id,
            verdict="UNTRUSTED", trust_score=round(st.trust, 2) if st else 0.0, risk_level="MEDIUM",
            reason_codes=[code], recommended_action="MONITOR",
            issued_at=obs.timestamp if self.use_obs_time else datetime.now(timezone.utc))
        self._store(v)
        if st:
            st.refused += 1
        self.log.emit("verdict", v.issued_at, observation_id=obs.observation_id, device_id=obs.device_id,
                      verdict=v.verdict, trust_score=v.trust_score, reason_codes=v.reason_codes,
                      action=v.recommended_action, detail=detail)
        return v

    def _peer_values(self, obs: Observation, ts: float) -> list[float]:
        fresh = self.cfg["peers"]["freshness_s"]
        probation = self.cfg["peers"].get("min_peer_readings", 0)
        vals = []
        for dev in self.groups.get((obs.sensor_type, obs.zone), ()):
            if dev == obs.device_id:
                continue
            st = self.devices[dev]
            if st.n_seen < probation:             # a device that just appeared cannot outvote the established ones
                continue
            if st.quarantined or st.trust <= self.cfg["scoring"]["untrusted_at_or_below"]:
                continue
            if st.last_ts is not None and abs(ts - st.last_ts) <= fresh and st.last_value is not None:
                vals.append(st.last_value)
        return vals

    def _age_s(self, other: DeviceState, obs: Observation, ts: float) -> float:
        """How old is the other device's last reading, in seconds. Ticks when both sides have them, else timestamps."""
        if obs.tick is not None and other.last_tick is not None:
            return abs(obs.tick - other.last_tick) * self.cfg.get("seconds_per_tick", 1.0)
        return abs(ts - other.last_ts) if other.last_ts is not None else float("inf")

    @staticmethod
    def _baseline(clean) -> Optional[float]:
        return statistics.median(clean) if len(clean) >= 1 else None

    def _pair_check(self, obs: Observation, st: DeviceState, ts: float, t: dict, own_base: Optional[float]):
        """Two redundant sensors measure the same thing. Returns (reason or None, corroborated).
        corroborated = a healthy backup agrees, so a fast change is real (a flash flood moves both sensors).
        On a disagreement the culprit is the one that moved away from its OWN recent history; if that is unclear, both
        are distrusted, because with only two sensors there is no majority."""
        tol = t.get("pair_tol", t["cross_tol"])
        fresh = self.cfg["peers"].get("pair_freshness_s", 15)
        probation = self.cfg["peers"].get("min_pair_readings", 1)       # a declared backup needs at least one clean reading
        sc = self.cfg["scoring"]
        corroborated = False
        for bid in st.backups:
            b = self.devices.get(bid)
            if (b is None or b.last_value is None or b.quarantined or b.unavailable or b.n_seen < probation
                    or b.trust <= sc["untrusted_at_or_below"] or self._age_s(b, obs, ts) > fresh):
                continue
            if abs(obs.value - b.last_value) <= tol:
                corroborated = True
                continue
            own_dev = abs(obs.value - own_base) if own_base is not None else None
            b_base = self._baseline(b.clean)
            b_dev = abs(b.last_value - b_base) if b_base is not None else None
            margin = 1.5
            if own_dev is not None and b_dev is not None and b_dev > own_dev * margin + tol * 0.25:
                continue                                    # the other sensor is the odd one out: it gets flagged on its turn
            return "BACKUP_DISAGREEMENT", False
        return None, corroborated

    def _backup_ref(self, obs: Observation, st: DeviceState, ts: float) -> Optional[float]:
        """Last value of a healthy, fresh backup (redundant twin sensor), or None."""
        fresh = self.cfg["peers"].get("pair_freshness_s", 15)
        probation = self.cfg["peers"].get("min_pair_readings", 1)
        for bid in st.backups:
            b = self.devices.get(bid)
            if (b is None or b.last_value is None or b.quarantined or b.unavailable or b.n_seen < probation
                    or b.trust <= self.cfg["scoring"]["untrusted_at_or_below"] or self._age_s(b, obs, ts) > fresh):
                continue
            return b.last_value
        return None

    def _drift_check(self, obs: Observation, st: DeviceState, t: dict, ref: Optional[float]) -> bool:
        """Slow drift: the gap between a sensor and its reference (peer median, else healthy backup) is small at every
        step, so range, rate and cross-sensor checks stay quiet, but it keeps the same sign and does not go away.
        A smoothed gap above the drift tolerance for several readings in a row is flagged. A REAL event moves the
        reference too (the gap stays near zero), so a flash flood is not mistaken for drift."""
        d = self.cfg.get("drift") or {}
        if not d.get("enabled", True) or ref is None:
            return False
        alpha = float(d.get("alpha", 0.2))
        tol = t.get("drift_tol", t["cross_tol"] * float(d.get("tol_ratio", 0.4)))
        st.drift = alpha * (obs.value - ref) + (1 - alpha) * st.drift
        st.drift_n = st.drift_n + 1 if abs(st.drift) > tol else 0
        return st.drift_n >= int(d.get("samples", 4))

    # ---- partner-reported conditions (not readings) -------------------------
    def mark_unavailable(self, device_id: str, status: str = "OFFLINE") -> None:
        """The partner says the device is offline (outage, or already isolated). Not an attack, trust is untouched."""
        st = self.devices.get(device_id)
        if st is None or st.unavailable:
            return
        st.unavailable = True
        self.log.emit("device_unavailable", device_id=device_id, status=status, action="MONITOR",
                      note="reported by the partner, not treated as an attack")

    def report_failure(self, obs_id: str, device_id: str, sensor_type: str, zone: str, status: str,
                       ts: Optional[datetime] = None) -> Optional[Verdict]:
        """The device itself reports FAILED (no value). Its stream is unusable: UNTRUSTED and quarantined, so the
        Brain falls back to its backup. A repeat of the same failure is idempotent."""
        if obs_id in self.verdicts:
            return self.verdicts[obs_id]
        ts = ts or datetime.now(timezone.utc)
        st = self.devices.get(device_id)
        if st is None:
            if self._registry_problem(Observation(observation_id=obs_id, device_id=device_id, sensor_type=sensor_type,
                                                   zone=zone, value=0.0, timestamp=ts)):
                return None
            st = DeviceState(sensor_type, zone)
            self.devices[device_id] = st
            self.zone_count[zone] += 1
            self.groups.setdefault((sensor_type, zone), set()).add(device_id)
        st.unavailable = False
        st.trust = 0.0
        newly = not st.quarantined
        st.quarantined = True
        self._n += 1
        v = Verdict(verdict_id=f"VRD-{self._n:06d}", observation_id=obs_id, device_id=device_id, verdict="UNTRUSTED",
                    trust_score=0.0, risk_level="HIGH", reason_codes=["SENSOR_FAILED", "DEVICE_QUARANTINED"],
                    recommended_action="QUARANTINE", issued_at=ts)
        self._store(v)
        self.log.emit("verdict", ts, observation_id=obs_id, device_id=device_id, verdict=v.verdict, trust_score=0.0,
                      reason_codes=v.reason_codes, action="QUARANTINE", detail=f"device reported status {status}")
        if newly:
            self.log.emit("device_quarantined", ts, device_id=device_id, observation_id=obs_id, trust_score=0.0)
            if self.on_contain:
                self.on_contain(device_id, "SENSOR_FAILED")
        return v

    def restore_device(self, device_id: str) -> bool:
        """An operator (or the recovery path) brings a quarantined device back. It starts on probation again."""
        st = self.devices.get(device_id)
        if st is None or not st.quarantined:
            return False
        st.quarantined = False
        st.trust = max(st.trust, self.cfg["scoring"]["release_at_or_above"])
        st.history.clear()
        st.n_seen = 0
        st.unavailable = False
        self.log.emit("device_restored", device_id=device_id, note="back on probation: it must send clean readings before it counts as a peer")
        if self.on_release:
            self.on_release(device_id)
        return True

    def reset_run(self, reason: str = "partner run changed") -> None:
        """The partner restarted its simulation: sequence numbers and device state start over."""
        self.devices.clear()
        self.groups.clear()
        self.zone_count.clear()
        self.verdicts.clear()
        self.log.emit("run_reset", note=reason)

    # ---- main entry ----------------------------------------------------
    def ingest(self, obs: Observation) -> Verdict:
        if obs.observation_id in self.verdicts:  # idempotent
            return self.verdicts[obs.observation_id]

        sc = self.cfg["scoring"]
        now = self._now(obs)

        # 1. A timestamp from the future would make every later real reading look like a replay.
        skew = self.sec.get("max_future_skew_s")
        if skew is not None and self.clock is not None and obs.timestamp.timestamp() > now + skew:
            return self._refuse(obs, "FUTURE_TIMESTAMP",
                                f"timestamp is {obs.timestamp.timestamp() - now:.0f}s ahead of the clock (limit {skew}s)",
                                self.devices.get(obs.device_id))

        # 2. An unknown device must be allowed to join before it gets any state.
        st = self.devices.get(obs.device_id)
        if st is None:
            problem = self._registry_problem(obs)
            if problem:
                return self._refuse(obs, "UNKNOWN_DEVICE", problem)
            rl = self.sec.get("rate_limit") or {}
            st = DeviceState(obs.sensor_type, obs.zone, arrivals=deque(maxlen=int(rl.get("max_messages") or 0) + 1))
            self.devices[obs.device_id] = st
            self.zone_count[obs.zone] += 1
            self.groups.setdefault((obs.sensor_type, obs.zone), set()).add(obs.device_id)

        # 3. Flood limiter, per device. Refused messages are not judged: anyone can forge a device id.
        rl = self.sec.get("rate_limit") or {}
        if rl.get("max_messages"):
            q = st.arrivals
            while q and now - q[0] >= rl.get("window_s", 1.0):
                q.popleft()
            q.append(now)
            if len(q) > rl["max_messages"]:
                if not st.flooding:
                    st.flooding = True
                    self.log.emit("flood_detected", obs.timestamp, device_id=obs.device_id,
                                  limit=rl["max_messages"], window_s=rl.get("window_s", 1.0), action="MONITOR")
                return self._refuse(obs, "MESSAGE_FLOOD",
                                    f"more than {rl['max_messages']} messages in {rl.get('window_s', 1.0)}s", st)
            st.flooding = False

        t = self._tcfg(obs.sensor_type)
        ts = obs.timestamp.timestamp()
        reasons: list[str] = []
        seq_known = obs.seq is not None and st.last_seq is not None
        if seq_known:     # sequence numbers decide, so many readings may share one timestamp; a clearly older timestamp is still a replay
            replay = st.last_ts is not None and (obs.seq <= st.last_seq or ts < st.last_ts)
        else:
            replay = st.last_ts is not None and ts <= st.last_ts

        if replay:
            # Rejected without touching the device's trust: an attacker must not be able
            # to degrade a healthy sensor just by replaying its old messages.
            reasons.append("REPLAY_OR_OUT_OF_ORDER")
            st.replays_rejected += 1
        else:
            if not (t["min"] <= obs.value <= t["max"]):
                reasons.append("OUT_OF_RANGE")
            if obs.tick is not None and st.last_tick is not None:
                dt = (obs.tick - st.last_tick) * self.cfg.get("seconds_per_tick", 1.0)
            else:
                dt = ts - st.last_ts if st.last_ts is not None else 0.0
            if obs.backup_ids:
                st.backups = list(obs.backup_ids)
            own_base = self._baseline(st.clean)
            pair_reason, corroborated = self._pair_check(obs, st, ts, t, own_base)
            # Noise divided by a few milliseconds looks like a huge jump: below min_rate_interval_s the rate check says
            # nothing, and the flood limiter is the right tool. Otherwise a flood could degrade a healthy sensor.
            if st.last_value is not None and dt >= self.cfg.get("min_rate_interval_s", 0.25):
                if abs(obs.value - st.last_value) / dt > t["max_rate"] and not corroborated:
                    reasons.append("IMPLAUSIBLE_RATE")        # a fast change that a healthy backup confirms is a real event
            st.history.append(obs.value)
            st.n_seen += 1
            peers = self._peer_values(obs, ts)
            ref = statistics.median(peers) if len(peers) >= self.cfg["peers"]["min_peers"] else self._backup_ref(obs, st, ts)
            st.ref_hist.append(statistics.median(peers) if peers else self._backup_ref(obs, st, ts))   # even one moving peer shows the world is not calm
            n = t.get("frozen_window", self.cfg["frozen_window"])
            if t["expect_noise"] and len(st.history) >= n and len(set(list(st.history)[-n:])) == 1:
                if not self.cfg.get("frozen_requires_motion"):
                    reasons.append("FROZEN_VALUE")
                else:
                    # A noise-free simulator holds a calm value for minutes: constant is only suspicious when the world around
                    # the sensor (its peers or its backup) moved during the same window.
                    refs = [r for r in list(st.ref_hist)[-n:] if r is not None]
                    tol = t.get("frozen_motion_tol", t["cross_tol"] / 16)
                    if len(refs) >= 2 and max(refs) - min(refs) > tol:
                        reasons.append("FROZEN_VALUE")
            if len(peers) >= self.cfg["peers"]["min_peers"]:
                if abs(obs.value - statistics.median(peers)) > t["cross_tol"]:
                    reasons.append("CROSS_SENSOR_CONTRADICTION")
            if pair_reason and "CROSS_SENSOR_CONTRADICTION" not in reasons:
                reasons.append(pair_reason)
            if self._drift_check(obs, st, t, ref) and not (
                    {"CROSS_SENSOR_CONTRADICTION", "BACKUP_DISAGREEMENT"} & set(reasons)):
                reasons.append("SLOW_DRIFT")             # the hard checks already say it when they fire
            if not reasons:
                st.clean.append(obs.value)               # only a reading that passed every check can shape the baseline
            st.last_ts, st.last_seq, st.last_value, st.last_tick = ts, obs.seq, obs.value, obs.tick
            if st.unavailable:
                st.unavailable = False
                self.log.emit("device_available_again", obs.timestamp, device_id=obs.device_id)
            if st.stale:
                st.stale = False
                self.log.emit("device_recovered", obs.timestamp, device_id=obs.device_id)

            if reasons:
                st.trust = max(0.0, st.trust - sum(sc["penalties"][r] for r in reasons))
            else:
                st.trust = min(1.0, st.trust + sc["recovery_per_clean_reading"])
            if not st.quarantined and st.trust < sc["quarantine_below"]:
                st.quarantined = True
                self.log.emit("device_quarantined", obs.timestamp, device_id=obs.device_id,
                              observation_id=obs.observation_id, trust_score=round(st.trust, 2))
                if self.on_contain:
                    self.on_contain(obs.device_id, ",".join(reasons))
            elif st.quarantined and st.trust >= sc["release_at_or_above"]:
                st.quarantined = False
                self.log.emit("device_released", obs.timestamp, device_id=obs.device_id)
                if self.on_release:
                    self.on_release(obs.device_id)

        hard = replay or any(r in HARD for r in reasons)
        # A reading that fails a check is itself UNTRUSTED, even when the device's score has not dropped yet:
        # City Brain must never act on a reading that Guardian has just flagged.
        flagged = bool(reasons) and not replay
        untrusted = hard or flagged or st.trust <= sc["untrusted_at_or_below"] or st.quarantined
        if st.quarantined and not replay:
            reasons.append("DEVICE_QUARANTINED")
        risk = "HIGH" if (st.quarantined or st.trust < sc["quarantine_below"]) else ("MEDIUM" if untrusted else "LOW")
        action = "QUARANTINE" if st.quarantined else ("ISOLATE" if any(r in HARD for r in reasons)
                                                        else ("MONITOR" if untrusted else "NONE"))
        self._n += 1
        v = Verdict(
            verdict_id=f"VRD-{self._n:06d}", observation_id=obs.observation_id, device_id=obs.device_id,
            verdict="UNTRUSTED" if untrusted else "TRUSTED", trust_score=round(st.trust, 2), risk_level=risk,
            reason_codes=reasons, recommended_action=action,
            issued_at=obs.timestamp if self.use_obs_time else datetime.now(timezone.utc))
        self._store(v)
        self.log.emit("verdict", v.issued_at, observation_id=obs.observation_id, device_id=obs.device_id,
                      verdict=v.verdict, trust_score=v.trust_score, reason_codes=reasons, action=action)
        return v

    def reject(self, reason: str, detail: str = "", observation_id: Optional[str] = None,
               device_id: Optional[str] = None) -> Verdict:
        """A payload that cannot be normalized is UNTRUSTED by default, never dropped or guessed.
        The named device's trust is NOT touched: anyone can send junk claiming to be a healthy sensor."""
        self._n += 1
        oid = observation_id or f"MALFORMED-{self._n:06d}"
        now = datetime.now(timezone.utc)
        self.log.emit("payload_rejected", now, observation_id=oid, device_id=device_id,
                      reason=reason, detail=detail)
        if oid in self.verdicts:
            return self.verdicts[oid]
        v = Verdict(verdict_id=f"VRD-{self._n:06d}", observation_id=oid, device_id=device_id or "unknown",
                    verdict="UNTRUSTED", trust_score=0.0, risk_level="MEDIUM",
                    reason_codes=["MALFORMED_PAYLOAD", reason], recommended_action="MONITOR", issued_at=now)
        self._store(v)
        return v

    def check_availability(self, now: datetime) -> list[str]:
        """A device that goes silent is flagged STALE (monitor), not quarantined: an outage is not an attack.
        Trust is untouched. Returns the devices that just became stale."""
        newly = []
        for dev, st in self.devices.items():
            limit = self._tcfg(st.sensor_type).get("stale_after_s")
            if limit is None or st.last_ts is None or st.stale:
                continue
            silent = now.timestamp() - st.last_ts
            if silent > limit:
                st.stale = True
                newly.append(dev)
                self.log.emit("device_stale", now, device_id=dev, silent_for_s=round(silent, 1),
                              action="MONITOR", note="availability failure, not treated as an attack")
        return newly

    # ---- persistence: trust survives a restart --------------------------------------------------------------
    def snapshot(self) -> dict:
        """What must survive a restart: who is distrusted or quarantined and each device's clean baseline.
        Ordering state (last seq and timestamp) is NOT saved: after a restart a fresh stream must be accepted."""
        return {"version": 1, "saved_at": datetime.now(timezone.utc).isoformat(),
                "devices": {dev: {"sensor_type": st.sensor_type, "zone": st.zone, "trust": round(st.trust, 4),
                                  "quarantined": st.quarantined, "n_seen": st.n_seen,
                                  "clean": [round(v, 4) for v in st.clean], "backups": list(st.backups)}
                            for dev, st in self.devices.items()}}

    def load_snapshot(self, data) -> int:
        """Restore a snapshot. The file is treated as untrusted input: every field is validated, bad entries are
        skipped, the registry and the caps still apply. Returns how many devices were restored."""
        import math
        import re
        from types import SimpleNamespace
        from .models import ID_PATTERN
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("devices"), dict):
            return 0
        n = 0
        for dev, d in data["devices"].items():
            try:
                if (not isinstance(dev, str) or not re.match(ID_PATTERN, dev) or dev in self.devices
                        or not isinstance(d, dict) or not isinstance(d["sensor_type"], str) or not isinstance(d["zone"], str)
                        or not (1 <= len(d["sensor_type"]) <= 64) or not (1 <= len(d["zone"]) <= 64)):
                    continue
                trust, n_seen = float(d["trust"]), int(d["n_seen"])
                clean = [float(v) for v in d.get("clean", [])][-20:]
                backups = [b for b in d.get("backups", []) if isinstance(b, str) and re.match(ID_PATTERN, b)][:4]
                if not (0.0 <= trust <= 1.0) or n_seen < 0 or not all(math.isfinite(v) for v in clean):
                    continue
                if self._registry_problem(SimpleNamespace(device_id=dev, zone=d["zone"])):
                    continue
                st = DeviceState(d["sensor_type"], d["zone"], trust=trust, quarantined=bool(d.get("quarantined", False)),
                                 n_seen=min(n_seen, 1_000_000), backups=backups)
                st.clean.extend(clean)
                self.devices[dev] = st
                self.zone_count[d["zone"]] += 1
                self.groups.setdefault((d["sensor_type"], d["zone"]), set()).add(dev)
                n += 1
            except (KeyError, TypeError, ValueError):
                continue
        if n:
            self.log.emit("state_restored", devices=n, note="trust state loaded from the last snapshot")
        return n

    def trust_of(self, device_id: str) -> Optional[dict]:
        st = self.devices.get(device_id)
        if st is None:
            return None
        return {"device_id": device_id, "trust_score": round(st.trust, 2), "quarantined": st.quarantined,
                "replays_rejected": st.replays_rejected, "refused": st.refused, "stale": st.stale,
                "drift": round(st.drift, 2)}
