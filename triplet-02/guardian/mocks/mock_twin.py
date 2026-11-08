"""Mock Twin (R1): replays readings and injects faults, so Guardian runs and is demoed on its own.
Each generator yields Observation objects at 1 reading/second/sensor, with deterministic noise."""
import random
from datetime import datetime, timedelta, timezone

from guardian.models import Observation

T0 = datetime(2026, 10, 16, 10, 0, 0, tzinfo=timezone.utc)
WL = ["WL-01", "WL-02", "WL-03", "WL-04", "WL-05"]


def _obs(dev, stype, zone, value, unit, t, seq, tag=""):
    return Observation(observation_id=f"OBS-{tag}{dev}-{seq:05d}", device_id=dev, sensor_type=stype,
                       zone=zone, value=round(value, 2), unit=unit, timestamp=T0 + timedelta(seconds=t), seq=seq)


def calm(seconds=60, seed=1):
    """T1: a normal day. Guardian must raise no alarms."""
    rng = random.Random(seed)
    for t in range(seconds):
        for d in WL:
            yield _obs(d, "water_level", "canal-north", 40 + rng.uniform(-1.5, 1.5), "cm", t, t)


def flood_spoof(seconds=45, spoof="WL-03", onset=10, seed=2, noisy=False):
    """T2: real flood on the canal; from `onset` one sensor reports ~30 cm.
    noisy=False: perfectly constant (frozen). noisy=True: a smarter attacker adds noise to look alive."""
    rng = random.Random(seed)
    for t in range(seconds):
        level = 40 if t < onset else min(190, 40 + 5 * (t - onset))
        for d in WL:
            if d == spoof and t >= onset:
                v = 30.0 + (rng.uniform(-1.0, 1.0) if noisy else 0.0)
            else:
                v = level + rng.uniform(-1.5, 1.5)
            yield _obs(d, "water_level", "canal-north", v, "cm", t, t)


def frozen_power(seconds=40, onset=15, seed=3):
    """T3: the pump-station load meter freezes at one value."""
    rng = random.Random(seed)
    for t in range(seconds):
        v = 52.0 if t >= onset else 50 + rng.uniform(-3, 3)
        yield _obs("PL-01", "power_load", "pump-station", v, "kW", t, t)


def replay_attack(seconds=40, replay_at=30, seed=4):
    """T6: at `replay_at` an attacker re-sends old WL-02 messages (old seq and timestamps)."""
    rng = random.Random(seed)
    history = []
    for t in range(seconds):
        for d in WL:
            o = _obs(d, "water_level", "canal-north", 40 + rng.uniform(-1.5, 1.5), "cm", t, t)
            if d == "WL-02":
                history.append(o)
            yield o
        if t == replay_at:
            for old in history[5:10]:
                yield old.model_copy(update={"observation_id": old.observation_id + "-R"})


def flood_spoof_noisy(**kw):
    return flood_spoof(noisy=True, **kw)


def camera_dropout(seconds=40, drop_at=20, seed=5):
    """T4: the intersection camera stops answering mid-incident. Availability failure, not an attack."""
    rng = random.Random(seed)
    for t in range(seconds):
        for d in WL:
            yield _obs(d, "water_level", "canal-north", 40 + rng.uniform(-1.5, 1.5), "cm", t, t)
        if t < drop_at:
            yield _obs("CAM-01", "traffic_camera", "intersection-1", 30 + rng.uniform(-5, 5), "%", t, t)


AQ = ["AQ-01", "AQ-02", "AQ-03", "AQ-04"]


def fire_aq_spoof(seconds=45, spoof="AQ-03", onset=10, seed=6):
    """T5: a fire raises air-quality readings; one compromised monitor keeps reporting a safe value (noisy)."""
    rng = random.Random(seed)
    for t in range(seconds):
        level = 40 if t < onset else min(260, 40 + 15 * (t - onset))
        for d in AQ:
            if d == spoof and t >= onset:
                v = 40 + rng.uniform(-1, 1)
            else:
                v = level + rng.uniform(-2, 2)
            yield _obs(d, "air_quality", "industrial-zone", v, "AQI", t, t)


def attack_during_flood(seconds=45, replay_at=30):
    """T9: a real flood, a noisy spoofed sensor and a replay burst, all at the same time."""
    history = []
    for o in flood_spoof(seconds=seconds, noisy=True):
        yield o
        if o.device_id == "WL-02":
            history.append(o)
        if o.device_id == "WL-05" and (o.timestamp - T0).total_seconds() == replay_at:
            for old in history[5:10]:
                yield old.model_copy(update={"observation_id": old.observation_id + "-R"})


def message_flood(seconds=30, victim="WL-02", onset=10, burst=150, seed=7):
    """T10: from `onset`, one device (or someone using its id) sends `burst` messages within a single second."""
    rng = random.Random(seed)
    for t in range(seconds):
        for d in WL:
            yield _obs(d, "water_level", "canal-north", 40 + rng.uniform(-1.5, 1.5), "cm", t, t)
        if t == onset:
            for k in range(burst):
                o = _obs(victim, "water_level", "canal-north", 40 + rng.uniform(-1.5, 1.5), "cm",
                         t + (k + 1) * 0.005, k, tag="F")
                yield o.model_copy(update={"seq": None, "observation_id": f"OBS-F{k:03d}-{victim}"})


FAKE_IDS = [f"WL-{n}" for n in range(70, 100)]       # thirty fake ids: ids cost an attacker nothing


def fake_device_injection(seconds=30, join=3, flip=10, seed=8):
    """T11: patient Sybil attack during a REAL flood (150 cm on the canal). From `join`, thirty fake sensors in the same zone
    report honest-looking values, so they earn full trust. At `flip` they all slowly ramp down to 20 cm (10 cm per second, under the rate-of-change limit) to outvote the five real
    sensors in the peer check, so the real ones look like liars and get quarantined."""
    rng = random.Random(seed)
    for t in range(seconds):
        for d in WL:
            yield _obs(d, "water_level", "canal-north", 150 + rng.uniform(-2, 2), "cm", t, t)
        if t >= join:
            for d in FAKE_IDS:
                v = max(20.0, 150 - 10 * (t - flip + 1)) + rng.uniform(-1, 1) if t >= flip else 150 + rng.uniform(-2, 2)   # slow ramp: stays under the rate limit
                yield _obs(d, "water_level", "canal-north", v, "cm", t, t)


def future_timestamp_poison(seconds=40, at=15, victim="WL-03", seed=9):
    """T12: at `at`, one message claims a timestamp one day in the future for `victim`. If it were accepted, every later
    real reading of that sensor would look older than the last one, i.e. like a replay."""
    rng = random.Random(seed)
    for t in range(seconds):
        for d in WL:
            yield _obs(d, "water_level", "canal-north", 40 + rng.uniform(-1.5, 1.5), "cm", t, t)
        if t == at:
            o = _obs(victim, "water_level", "canal-north", 40.0, "cm", t + 86400, 99999, tag="POISON-")
            yield o
