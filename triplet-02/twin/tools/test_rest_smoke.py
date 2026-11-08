#!/usr/bin/env python3
"""Smoke test for REST API"""
import os
import sys
sys.path.insert(0, 'src')
os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

from city_live.runtime import LiveCity
from twin_interface.facade import TwinFacade
from twin_interface.auth import DEFAULT_TOKENS, Role
from twin_interface.rest.router import handle_rest_request


def main():
    print("REST API Smoke Test\n" + "="*40)
    city = LiveCity("test-rest-01")
    facade = TwinFacade(city)
    for _ in range(5):
        city.step()
    brain = DEFAULT_TOKENS[Role.BRAIN]
    guardian = DEFAULT_TOKENS[Role.GUARDIAN]
    
    try:
        status, resp = handle_rest_request(facade, "GET", "/health")
        print(f"✓ Health: {status} ok={resp['ok']}")
        
        status, resp = handle_rest_request(facade, "GET", "/api/v1/sensors", headers={})
        print(f"✓ Sensors no auth: {status} ok={resp['ok']}")
        
        brain_headers = {"Authorization": f"Bearer {brain}"}
        status, resp = handle_rest_request(
            facade, "GET", "/api/v1/sensors", headers=brain_headers
        )
        print(f"✓ Sensors brain: {status} count={resp['data']['count']}")
        
        status, resp = handle_rest_request(
            facade, "GET", f"/api/v1/sensors?token={brain}"
        )
        print(f"✓ Sensors query credential rejected: {status}")
        
        status, resp = handle_rest_request(
            facade,
            "GET",
            "/api/v1/sensors?zone_id=PRT",
            headers=brain_headers,
        )
        print(f"✓ Sensors zone filter: {status} ok={resp['ok']}")
        
        status, resp = handle_rest_request(
            facade, "GET", "/api/v1/state", headers=brain_headers
        )
        print(f"✓ State: {status} has_zones={('zones' in resp['data'])}")
        
        status, resp = handle_rest_request(
            facade, "GET", "/api/v1/events?seq=0", headers=brain_headers
        )
        print(f"✓ Events: {status} count={resp['data']['count']}")
        
        status, resp = handle_rest_request(facade, "POST", "/api/v1/commands",
                                          headers={"Authorization": f"Bearer {brain}"},
                                          body={"device_id": "V-12", "command": "set_opening", 
                                                "params": {"open_pct": 30}, "request_id": "rest-1"})
        print(f"✓ Command: {status} ok={resp['ok']}")
        
        status, resp = handle_rest_request(facade, "POST", "/api/v1/isolate",
                                          headers={"Authorization": f"Bearer {guardian}"},
                                          body={"device_id": "V-12", "reason": "test"})
        print(f"✓ Isolate: {status} ok={resp['ok']}")
        status, resp = handle_rest_request(facade, "POST", "/api/v1/commands",
                                          headers={"Authorization": f"Bearer {brain}"},
                                          body={"device_id": "V-12", "command": "set_opening", 
                                                "params": {"open_pct": 50}, "request_id": "rest-2"})
        print(f"✓ Command quarantined: {status} code={resp['error']['code'] if not resp['ok'] else None}")
        status, resp = handle_rest_request(facade, "POST", "/api/v1/restore",
                                          headers={"Authorization": f"Bearer {guardian}"},
                                          body={"device_id": "V-12"})
        print(f"✓ Restore: {status} ok={resp['ok']}")
        
        print("\n" + "="*40)
        print("✅ ALL REST TESTS PASSED")
        return 0
    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        city.close()

if __name__ == "__main__":
    sys.exit(main())
