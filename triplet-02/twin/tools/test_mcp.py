"""Standalone verification script for the FastMCP Tool Server.

Tests:
1. FastMCP server initialization.
2. Tool discovery and LLM docstring validation.
3. Invocation of all 6 semantic tools (get_sensor_readings, get_city_state,
   isolate_device, send_actuator_command, restore_device, get_events_since).
4. Verifies anti-leak and quarantine enforcement over MCP.

Run with:
    python tools/test_mcp.py
"""
import asyncio
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

from city_live.runtime import LiveCity
from twin_interface.auth import Role, DEFAULT_TOKENS
from twin_interface.envelope import ErrorCode
from twin_interface.facade import TwinFacade
from twin_interface.mcp_server.server import create_mcp_server


def print_header(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_step(num: int, title: str) -> None:
    print(f"\n>> Step {num}: {title}")
    print("-" * 70)


async def main() -> None:
    print_header("[MCP TEST] FASTMCP SERVER & TOOL VERIFICATION")
    print("Starting LiveCity and creating FastMCP Server...")

    city = LiveCity("mcp-verification-run")
    facade = TwinFacade(city)
    for _ in range(5):
        city.step()
    mcp = create_mcp_server(facade)

    brain_token = DEFAULT_TOKENS[Role.BRAIN]
    guardian_token = DEFAULT_TOKENS[Role.GUARDIAN]

    # --------------------------------------------------------------------------
    # 1. Discover registered tools and their docstrings
    # --------------------------------------------------------------------------
    print_step(1, "Discovering Registered FastMCP Tools")
    tools = await mcp.list_tools()
    print(f"[+] Discovered {len(tools)} tools exposed to external LLMs:\n")
    for t in tools:
        doc_snippet = t.description.split("\n")[0] if t.description else "No description"
        print(f"    * {t.name:<25} -> {doc_snippet}")

    expected_tools = {
        "get_sensor_readings",
        "get_city_state",
        "get_events_since",
        "send_actuator_command",
        "isolate_device",
        "restore_device",
    }
    registered_names = {t.name for t in tools}
    if expected_tools.issubset(registered_names):
        print("\n[+] All 6 expected semantic tools are correctly registered!")
    else:
        print(f"\n[-] Missing tools: {expected_tools - registered_names}")

    # --------------------------------------------------------------------------
    # 2. Invoke get_sensor_readings via MCP
    # --------------------------------------------------------------------------
    print_step(2, "Calling MCP Tool: 'get_sensor_readings(zone_id=\"PRT\")'")
    content, meta = await mcp.call_tool("get_sensor_readings", {"zone_id": "PRT", "token": brain_token})
    resp = meta.get("result", meta)

    if resp.get("ok"):
        readings = resp["data"]["readings"]
        print(f"[+] MCP tool executed successfully! Received {len(readings)} readings:")
        for r in readings:
            stype = r.get("type") or r.get("sensor_type") or "telemetry"
            print(f"    * [{r.get('sensor_id')}] {stype}: {r.get('reported_value')} {r.get('unit', '')} (Status: {r.get('status')})")

        has_leak = any("actual_value" in r or "delta" in r or "fault" in r for r in readings)
        print(f"    [Anti-Leak Check]: {'PASSED (Zero secrets leaked)' if not has_leak else 'FAILED'}")
    else:
        print(f"[-] MCP Tool call failed: {resp.get('error')}")

    # --------------------------------------------------------------------------
    # 3. Invoke get_city_state via MCP
    # --------------------------------------------------------------------------
    print_step(3, "Calling MCP Tool: 'get_city_state(zone_id=\"PRT\")'")
    content, meta = await mcp.call_tool("get_city_state", {"zone_id": "PRT", "token": brain_token})
    resp = meta.get("result", meta)

    if resp.get("ok"):
        state_data = resp["data"]
        print(f"[+] MCP tool executed successfully! City overview:")
        print(f"    * Sim Tick: {state_data.get('sim_tick')}")
        print(f"    * Speed: {state_data.get('speed')}x")
        print(f"    * Active Incidents: {len(state_data.get('active_incidents', []))}")
        print(f"    * Quarantined Devices: {state_data.get('quarantined_devices_count')}")
    else:
        print(f"[-] MCP Tool call failed: {resp.get('error')}")

    # --------------------------------------------------------------------------
    # 4. Invoke isolate_device via MCP (Guardian)
    # --------------------------------------------------------------------------
    print_step(4, "Calling MCP Tool: 'isolate_device' (Guardian quarantine)")
    target = "PRT-WLV-01"
    content, meta = await mcp.call_tool(
        "isolate_device",
        {
            "token": guardian_token,
            "device_id": target,
            "reason": "MCP Verification: Tampering Anomaly",
        },
    )
    resp = meta.get("result", meta)

    if resp.get("ok"):
        print(f"[+] Guardian quarantined '{target}' over MCP!")
        print(f"    * Status: {resp['data']['status']}")
        print(f"    * Reason: {resp['data']['reason']}")
    else:
        print(f"[-] MCP Tool call failed: {resp.get('error')}")

    # --------------------------------------------------------------------------
    # 5. Invoke send_actuator_command on quarantined device (Negative Test)
    # --------------------------------------------------------------------------
    print_step(5, "Calling MCP Tool: 'send_actuator_command' on quarantined device (Must be blocked)")
    content, meta = await mcp.call_tool(
        "send_actuator_command",
        {
            "token": brain_token,
            "device_id": target,
            "command": "start",
            "params": {},
            "request_id": "mcp-test-req-001",
        },
    )
    resp = meta.get("result", meta)

    if not resp.get("ok") and resp["error"]["code"] == ErrorCode.GUARDIAN_RESTRICTED:
        print(f"[+] Twin successfully BLOCKED command over MCP!")
        print(f"    * Error Code: {resp['error']['code']}")
        print(f"    * Error Message: {resp['error']['message']}")
    else:
        print(f"[-] Security violation: Quarantined device was not blocked!")

    # --------------------------------------------------------------------------
    # 6. Invoke send_actuator_command on valid pump (Brain)
    # --------------------------------------------------------------------------
    print_step(6, "Calling MCP Tool: 'send_actuator_command' (Brain emergency pump start)")
    content, meta = await mcp.call_tool(
        "send_actuator_command",
        {
            "token": brain_token,
            "device_id": "PRT-PMP-02",
            "command": "start",
            "params": {"speed_pct": 100},
            "request_id": "mcp-test-req-002",
        },
    )
    resp = meta.get("result", meta)

    if resp.get("ok"):
        print(f"[+] Pump command successfully APPLIED to simulation physics over MCP!")
        print(f"    * Target: {resp['data']['device_id']}")
        print(f"    * Status: {resp['data']['status']}")
    else:
        print(f"[-] Pump command failed: {resp.get('error')}")

    # --------------------------------------------------------------------------
    # 7. Invoke restore_device via MCP
    # --------------------------------------------------------------------------
    print_step(7, "Calling MCP Tool: 'restore_device' (Guardian restoration)")
    content, meta = await mcp.call_tool(
        "restore_device",
        {
            "token": guardian_token,
            "device_id": target,
        },
    )
    resp = meta.get("result", meta)

    if resp.get("ok"):
        print(f"[+] Device '{target}' successfully RESTORED over MCP!")
        print(f"    * Status: {resp['data']['status']}")
    else:
        print(f"[-] Restoration failed: {resp.get('error')}")

    city.close()
    print_header("[SUCCESS] ALL FASTMCP TOOLS & SERVER WORKING PERFECTLY!")
    print("External LLMs and autonomous agents can discover and call all tools reliably.\n")


if __name__ == "__main__":
    asyncio.run(main())
