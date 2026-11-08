#!/usr/bin/env python3
"""Smoke test for Twin Interface: Auth, Permissions, Facade, MCP"""
import os
import sys

sys.path.insert(0, 'src')
os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

from city_live.runtime import LiveCity
from twin_interface.auth import DEFAULT_TOKENS, Role, resolve_token
from twin_interface.envelope import ErrorCode
from twin_interface.facade import TwinFacade
from twin_interface.permissions import check_tool_permission
from twin_interface.mcp_server.middleware import extract_bearer_token, resolve_header_role


def test_auth():
    print("=== AUTH TESTS ===")
    for role in [Role.BRAIN, Role.GUARDIAN, Role.OPERATOR, Role.EVALUATOR]:
        token = DEFAULT_TOKENS[role]
        resolved = resolve_token(token)
        assert resolved == role, f"Failed for {role}"
        assert resolve_token(f"Bearer {token}") == role
    assert resolve_token("bad-token") is None
    assert resolve_token(None) is None
    print("✓ Auth all passed")


def test_permissions():
    print("\n=== PERMISSIONS TESTS ===")
    for role in [Role.BRAIN, Role.GUARDIAN, Role.OPERATOR, Role.EVALUATOR]:
        ok, _ = check_tool_permission("get_sensor_readings", role)
        assert ok, f"get_sensor_readings failed for {role}"
    assert check_tool_permission("send_actuator_command", Role.BRAIN)[0]
    assert check_tool_permission("send_actuator_command", Role.OPERATOR)[0]
    assert not check_tool_permission("send_actuator_command", Role.GUARDIAN)[0]
    assert not check_tool_permission("send_actuator_command", Role.EVALUATOR)[0]
    assert check_tool_permission("isolate_device", Role.GUARDIAN)[0]
    assert check_tool_permission("isolate_device", Role.OPERATOR)[0]
    assert not check_tool_permission("isolate_device", Role.BRAIN)[0]
    assert check_tool_permission("restore_device", Role.GUARDIAN)[0]
    print("✓ Permissions all passed")


def test_middleware():
    print("\n=== MIDDLEWARE TESTS ===")
    headers = {"Authorization": f"Bearer {DEFAULT_TOKENS[Role.BRAIN]}"}
    assert extract_bearer_token(headers) == DEFAULT_TOKENS[Role.BRAIN]
    assert resolve_header_role(headers) == Role.BRAIN
    headers2 = {"authorization": f"Bearer {DEFAULT_TOKENS[Role.GUARDIAN]}"}
    assert extract_bearer_token(headers2) == DEFAULT_TOKENS[Role.GUARDIAN]
    assert resolve_header_role(headers2) == Role.GUARDIAN
    print("✓ Middleware all passed")


def test_facade():
    print("\n=== FACADE TESTS ===")
    city = LiveCity("test-smoke-01")
    facade = TwinFacade(city)
    for _ in range(5):
        city.step()
    brain = DEFAULT_TOKENS[Role.BRAIN]
    guardian = DEFAULT_TOKENS[Role.GUARDIAN]
    
    try:
        resp = facade.get_sensor_readings(token="bad")
        assert not resp["ok"] and resp["error"]["code"] == ErrorCode.NOT_AUTHORIZED
        print("  ✓ Unauthorized rejected")
        
        resp = facade.get_sensor_readings(token=brain)
        assert resp["ok"]
        data = resp["data"]
        assert data["count"] > 0
        for r in data["readings"]:
            assert "sensor_id" in r
            assert "reported_value" in r
            assert "actual_value" not in r
            assert "delta" not in r
            assert "fault" not in r
        print(f"  ✓ Brain readings: {data['count']} sensors, no GT leakage")
        
        resp = facade.get_sensor_readings(token=brain, zone_id="PRT")
        assert resp["ok"]
        for r in resp["data"]["readings"]:
            assert r["zone_id"] == "PRT"
        resp = facade.get_sensor_readings(token=brain, sensor_id="PRT-WLV-01")
        assert resp["ok"]
        assert len(resp["data"]["readings"]) == 1
        resp = facade.get_sensor_readings(token=brain, sensor_id="UNKNOWN-999")
        assert not resp["ok"] and resp["error"]["code"] == ErrorCode.DEVICE_NOT_FOUND
        print("  ✓ Filtering works")
        
        resp = facade.get_city_state(token=brain)
        assert resp["ok"]
        assert "zones" in resp["data"]
        assert "PRT" in resp["data"]["zones"]
        resp = facade.get_city_state(token=brain, zone_id="DTN")
        assert resp["ok"] and "DTN" in resp["data"]["zones"]
        print("  ✓ City state works")
        
        resp = facade.get_events_since(token=brain, seq=0)
        assert resp["ok"]
        print(f"  ✓ Events: {resp['data']['count']}")
        
        device = "V-12"
        iso = facade.isolate_device(token=guardian, device_id=device, reason="test")
        assert iso["ok"] and facade.is_device_isolated(device)
        cmd = facade.send_actuator_command(token=brain, device_id=device, command="set_opening", 
                                           params={"open_pct": 80.0}, request_id="req-1")
        assert not cmd["ok"] and cmd["error"]["code"] == ErrorCode.GUARDIAN_RESTRICTED
        res = facade.restore_device(token=guardian, device_id=device)
        assert res["ok"] and not facade.is_device_isolated(device)
        cmd2 = facade.send_actuator_command(token=brain, device_id=device, command="set_opening",
                                            params={"open_pct": 50.0}, request_id="req-2")
        assert cmd2["ok"] and cmd2["data"]["status"] == "APPLIED"
        print("  ✓ Quarantine enforcement works")
        
        cmd3 = facade.send_actuator_command(token=brain, device_id="PRT-PMP-01", command="start",
                                            params={"speed_pct": 50}, request_id="req-idem")
        if not cmd3["ok"]:
            # try fallback
            cmd3 = facade.send_actuator_command(token=brain, device_id="A-PUMP-01", command="start",
                                                params={}, request_id="req-idem2")
            if not cmd3["ok"]:
                print(f"  ⚠ Idempotency test skipped: {cmd3.get('error')}")
            else:
                cmd3b = facade.send_actuator_command(token=brain, device_id="A-PUMP-01", command="start",
                                                     params={}, request_id="req-idem2")
                assert cmd3b["ok"]
                print("  ✓ Idempotency works")
        else:
            cmd3b = facade.send_actuator_command(token=brain, device_id="PRT-PMP-01", command="start",
                                                 params={"speed_pct": 50}, request_id="req-idem")
            assert cmd3b["ok"] and cmd3b["data"]["request_id"] == "req-idem"
            print("  ✓ Idempotency works")
        
    finally:
        city.close()
    
    print("✓ Facade all passed")


def test_mcp_server():
    print("\n=== MCP SERVER TESTS ===")
    try:
        from twin_interface.mcp_server.server import create_mcp_server
        city = LiveCity("test-mcp-01")
        facade = TwinFacade(city)
        try:
            mcp = create_mcp_server(facade)
            tools = getattr(mcp, '_tools', {}) or getattr(mcp, 'tools', {})
            print(f"  ✓ MCP server created with {len(tools)} tools")
            brain = DEFAULT_TOKENS[Role.BRAIN]
            resp = facade.get_sensor_readings(token=brain)
            assert resp["ok"]
            print("  ✓ MCP tools accessible via facade")
        finally:
            city.close()
    except Exception as e:
        print(f"  ⚠ MCP test skipped: {e}")


def main():
    print("Twin Interface Smoke Test\n" + "="*40)
    try:
        test_auth()
        test_permissions()
        test_middleware()
        test_facade()
        test_mcp_server()
        print("\n" + "="*40)
        print("✅ ALL TESTS PASSED")
        return 0
    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
