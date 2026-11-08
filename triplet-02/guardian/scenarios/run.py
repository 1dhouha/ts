"""One command runs a scenario, writes logs/<id>-<time>.log (R2) and prints what happened.
Usage:  PYTHONPATH=src:. python -m scenarios.run t2        (or: all)"""
import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # so `python -m scenarios.run` works without PYTHONPATH

from guardian.containment import LoggingTwinClient
from guardian.engine import GuardianEngine, load_config
from guardian.eventlog import EventLog
from mocks import mock_twin as m
from mocks import twin_rest_sim as ts
from mocks.mock_brain import GuardianUnavailable, MockBrain

SCENARIOS = {
    "t1": dict(name="Calm day, no incident", gen=m.calm),
    "t2": dict(name="Fake flood: WL-03 stuck at 30 cm", gen=m.flood_spoof),
    "t2b": dict(name="Fake flood, noisy spoof (evades frozen check)", gen=m.flood_spoof_noisy),
    "t3": dict(name="Frozen power-load meter", gen=m.frozen_power),
    "t4": dict(name="Camera dropout is not an attack", gen=m.camera_dropout),
    "t5": dict(name="Fire with a compromised air-quality monitor", gen=m.fire_aq_spoof),
    "t6": dict(name="Replayed readings", gen=m.replay_attack),
    "t7": dict(name="Brain wants a quarantined device", gen=m.flood_spoof_noisy, brain=True),
    "t8": dict(name="Guardian unreachable mid-incident", gen=m.flood_spoof_noisy, brain=True, outage=(20, 35)),
    "t9": dict(name="Real flood and attack at the same time", gen=m.attack_during_flood, brain=True),
    "t10": dict(name="Message flood from one device", gen=m.message_flood),
    "t11": dict(name="Fake-device injection during a real flood (registry strict)", gen=m.fake_device_injection,
                cfg=lambda c: c["security"]["registry"].update(mode="strict", allowed_ids=m.WL)),
    "t12": dict(name="Future-timestamp poisoning", gen=m.future_timestamp_poison),
    "t13": dict(name="Twin contract: fake flood at the port (their T5)", gen=ts.twin_fake_flood, profile="twin"),
    "t14": dict(name="Twin contract: real flash flood is not an attack (their SCN-02)", gen=ts.twin_real_flash_flood, profile="twin"),
}


class Run:
    """One scenario wired end to end: mock Twin -> Guardian -> (mock Brain). Used by the CLI, the tests and the live demo."""

    def __init__(self, sid: str, log: EventLog):
        self.sid, self.sc, self.log = sid, SCENARIOS[sid], log
        self.twin = LoggingTwinClient()
        cfg = load_config(profile=self.sc.get("profile", ""))   # base config unless the scenario names a profile
        if self.sc.get("cfg"):
            self.sc["cfg"](cfg)                       # a scenario may switch on a stricter setting (for example the registry)
        # Simulated clock: it follows plausible observations only, so a forged far-future timestamp cannot move "now".
        self.sim_now = m.T0
        self.eng = GuardianEngine(cfg, on_contain=lambda dev, why: self.twin.quarantine_device(dev, why),
                                  eventlog=log, use_obs_time=True, clock=lambda: self.sim_now)
        self.clock = {"t": 0.0}
        self.verdicts, self.last_ts, self.brain = [], m.T0, None
        if self.sc.get("brain"):
            outage = self.sc.get("outage")

            def get_verdict(oid):
                if outage and outage[0] <= self.clock["t"] < outage[1]:
                    raise GuardianUnavailable(oid)
                return self.eng.verdicts[oid].model_dump(mode="json")
            self.brain = MockBrain(get_verdict, log)
        log.emit("scenario_start", m.T0, scenario=sid, name=self.sc["name"])

    def feed(self, obs):
        skew = self.eng.sec.get("max_future_skew_s")
        if skew is None or obs.timestamp <= self.sim_now + timedelta(seconds=skew):
            self.sim_now = max(self.sim_now, obs.timestamp)
            self.last_ts = max(self.last_ts, obs.timestamp)
        self.clock["t"] = (self.sim_now - m.T0).total_seconds()
        self.log.emit("reading", obs.timestamp, layer="twin", observation_id=obs.observation_id,
                      device_id=obs.device_id, value=obs.value, unit=obs.unit)
        v = self.eng.ingest(obs)
        self.verdicts.append(v)
        if self.brain:
            self.brain.observe(obs)
        return v

    def finish(self):
        self.eng.check_availability(self.last_ts)
        if self.sid == "t7" and self.brain:
            self.brain.consider("WL-03", self.eng.trust_of, self.last_ts)
            self.brain.consider("WL-02", self.eng.trust_of, self.last_ts)
        self.log.emit("scenario_end", self.last_ts, scenario=self.sid)


def run(sid: str, log_dir: str = "logs", pace=None) -> dict:
    Path(log_dir).mkdir(exist_ok=True)
    path = f"{log_dir}/{sid}-{datetime.now():%Y%m%d-%H%M%S}.log"
    r = Run(sid, EventLog(path))
    for obs in r.sc["gen"]():
        r.feed(obs)
        if pace:
            pace(obs)
    r.finish()
    return {"id": sid, "name": r.sc["name"], "engine": r.eng, "verdicts": r.verdicts, "twin": r.twin,
            "brain": r.brain, "log": path}


def first_untrusted(verdicts, device):
    for v in verdicts:
        if v.device_id == device and v.verdict == "UNTRUSTED":
            return v
    return None


if __name__ == "__main__":
    ids = list(SCENARIOS) if len(sys.argv) > 1 and sys.argv[1] == "all" else [sys.argv[1] if len(sys.argv) > 1 else "t2"]
    for sid in ids:
        r = run(sid)
        bad = [v for v in r["verdicts"] if v.verdict == "UNTRUSTED"]
        print(f"{sid} {r['name']}: {len(r['verdicts'])} readings, {len(bad)} UNTRUSTED, "
              f"containment {[c[:2] for c in r['twin'].calls]}, log {r['log']}")
        if r["brain"]:
            print("   brain decisions:", [(d["action"], d.get("device_id") or d.get("reason")) for d in r["brain"].decisions])
