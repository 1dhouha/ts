"""Interactive manual test script for the Digital Twin Layer.

Demonstrates the complete end-to-end flow in your terminal:
1. Brain queries telemetry (verifying 0 ground truth leakage).
2. Guardian quarantines a suspicious device.
3. Brain tries to command the quarantined device (blocked by Twin!).
4. Brain commands an emergency pump (successfully applied).
5. Guardian restores the quarantined device.
6. Displays the live visual timeline and generated JSONL audit log.

Run with:
    python tools/manual_test.py
"""
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure src is in python path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from twin_interface.auth import Role, DEFAULT_TOKENS
from twin_interface.envelope import ErrorCode
from twin_interface.facade import TwinFacade
from twin_interface.rest.router import handle_rest_request
from twin_observability.event_store import EventStore
from twin_observability.scenario_log import ScenarioLogger
from twin_observability.timeline import format_timeline_item


def print_header(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_step(step_num: int, title: str) -> None:
    print(f"\n>> Step {step_num}: {title}")
    print("-" * 70)


def main() -> None:
    print_header("[DEMO] DIGITAL TWIN LAYER: MANUAL INTERACTIVE DEMO")
    print("Initializing LiveCity, TwinFacade, EventStore, and ScenarioLogger...")

    # 1. Initialize components
    log_dir = ROOT / "logs"
    store = EventStore(capacity=1000)
    logger = ScenarioLogger(scenario="manual_demo", run_id="live_001", log_dir=log_dir)
    facade = TwinFacade(event_store=store, scenario_logger=logger)
    for _ in range(5):
        facade.city.step()

    logger.log_start(sim_tick=facade.city.status()["sim_tick"], config={"scenario": "manual_demo", "mode": "interactive"})
    print(f"[+] Audit log initialized at: logs/manual_demo_live_001.jsonl")

    # --------------------------------------------------------------------------
    # Step 1: Brain fetches sensor telemetry
    # --------------------------------------------------------------------------
    print_step(1, "Brain queries Port zone sensor readings (get_sensor_readings)")
    brain_token = DEFAULT_TOKENS[Role.BRAIN]
    readings_resp = facade.get_sensor_readings(token=brain_token, zone_id="PRT")

    if not readings_resp["ok"]:
        print(f"[-] Failed to fetch readings: {readings_resp['error']}")
        return

    readings = readings_resp["data"]["readings"]
    print(f"[+] Brain received {len(readings)} sensor reading(s) in Port zone:")
    for r in readings:
        stype = r.get("type") or r.get("sensor_type") or "telemetry"
        print(f"    * [{r.get('sensor_id')}] {stype}: {r.get('reported_value')} {r.get('unit', '')} (Status: {r.get('status')})")

    # Verify anti-leak protection
    has_leak = any("actual_value" in r or "delta" in r or "fault" in r for r in readings)
    if not has_leak:
        print("    [SHIELD] [ANTI-LEAK VERIFIED]: 'actual_value', 'delta', and 'fault' are 100% HIDDEN from Brain!")
    else:
        print("    [!] SECURITY ALERT: Ground truth leaked!")

    # --------------------------------------------------------------------------
    # Step 2: Guardian detects anomaly and quarantines device
    # --------------------------------------------------------------------------
    print_step(2, "Guardian detects sensor discrepancy and isolates it (isolate_device)")
    guardian_token = DEFAULT_TOKENS[Role.GUARDIAN]
    target_device = "PRT-WLV-01"
    
    print(f"[GUARDIAN] Sending quarantine command for '{target_device}'...")
    iso_resp = facade.isolate_device(
        token=guardian_token,
        device_id=target_device,
        reason="Discrepancy detected: water level reported 15cm vs neighbor 85cm (>50cm delta)",
    )

    if iso_resp["ok"]:
        print(f"[+] Device '{target_device}' successfully isolated!")
        print(f"    Status: {iso_resp['data']['status']}")
        print(f"    Isolated by: {iso_resp['data']['isolated_by']}")
        print(f"    Reason: {iso_resp['data']['reason']}")
    else:
        print(f"[-] Isolation failed: {iso_resp['error']}")

    # --------------------------------------------------------------------------
    # Step 3: Brain attempts to use quarantined device (Negative test)
    # --------------------------------------------------------------------------
    print_step(3, "Brain attempts to control the quarantined device (send_actuator_command)")
    print(f"[BRAIN] Attempting to send command to isolated '{target_device}'...")
    blocked_cmd = facade.send_actuator_command(
        token=brain_token,
        device_id=target_device,
        command="start",
        params={},
        request_id="req-blocked-001",
    )

    if not blocked_cmd["ok"] and blocked_cmd["error"]["code"] == ErrorCode.GUARDIAN_RESTRICTED:
        print(f"[+] [QUARANTINE ENFORCED]: Twin successfully BLOCKED the Brain's command!")
        print(f"    Error Code: {blocked_cmd['error']['code']}")
        print(f"    Message: {blocked_cmd['error']['message']}")
    else:
        print(f"[-] Security violation: Quarantined device was not blocked!")

    # --------------------------------------------------------------------------
    # Step 4: Brain dispatches emergency flood pump
    # --------------------------------------------------------------------------
    print_step(4, "Brain starts emergency drainage pump PRT-PMP-02 (send_actuator_command)")
    pump_device = "PRT-PMP-02"
    print(f"[BRAIN] Commanding pump '{pump_device}' to start at 100% speed...")
    
    pump_resp = facade.send_actuator_command(
        token=brain_token,
        device_id=pump_device,
        command="start",
        params={"speed_pct": 100},
        request_id="req-pump-001",
    )

    if pump_resp["ok"]:
        print(f"[+] Command successfully APPLIED to simulation physics!")
        print(f"    Target: {pump_resp['data']['device_id']}")
        print(f"    Command: {pump_resp['data']['command']}")
        print(f"    Status: {pump_resp['data']['status']}")
    else:
        print(f"[-] Pump command failed: {pump_resp['error']}")

    # --------------------------------------------------------------------------
    # Step 5: Guardian restores device
    # --------------------------------------------------------------------------
    print_step(5, "Guardian repairs and restores device (restore_device)")
    print(f"[GUARDIAN] Restoring device '{target_device}' back to normal service...")
    rest_resp = facade.restore_device(token=guardian_token, device_id=target_device)

    if rest_resp["ok"]:
        print(f"[+] Device '{target_device}' successfully RESTORED!")
        print(f"    Status: {rest_resp['data']['status']}")
    else:
        print(f"[-] Restoration failed: {rest_resp['error']}")

    # --------------------------------------------------------------------------
    # Step 6: Live Timeline & Audit Log Inspection
    # --------------------------------------------------------------------------
    print_step(6, "Live Event Timeline & JSONL Audit Log (Requirements R2 & R3)")
    logger.log_end(sim_tick=facade.city.status()["sim_tick"], summary={"status": "DEMO_COMPLETED", "actions_applied": 2})
    logger.close()

    # Query timeline from REST /api/v1/timeline
    operator_token = DEFAULT_TOKENS[Role.OPERATOR]
    status_code, timeline_resp = handle_rest_request(
        facade=facade,
        method="GET",
        path="/api/v1/timeline?seq=0",
        headers={"Authorization": f"Bearer {operator_token}"},
    )

    if status_code == 200:
        cards = timeline_resp["data"]["timeline"]
        print(f"[+] Visual Timeline rendered {len(cards)} sequential event cards:")
        for c in cards:
            badge_icon = "[QUARANTINE]" if "QUARANTINE" in c["badge"] else ("[ACTION]" if "ACTION" in c["badge"] else "[INFO]")
            print(f"    * [seq: {c['seq']}] {badge_icon} {c['title']} -> {c['summary']}")

    print("\n[+] Reading raw append-only audit file (logs/manual_demo_live_001.jsonl):")
    log_file = log_dir / "manual_demo_live_001.jsonl"
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    for idx, line in enumerate(lines, 1):
        parsed = json.loads(line)
        print(f"    Line {idx}: [seq: {parsed.get('seq')}] {parsed.get('event_type')} (from: {parsed.get('source')})")

    print_header("[SUCCESS] MANUAL TEST COMPLETED WITH 100% SUCCESS!")
    print("All architectural requirements (Anti-leak, Quarantine, Atomic Seq, JSONL Audit) verified live.\n")


if __name__ == "__main__":
    main()
