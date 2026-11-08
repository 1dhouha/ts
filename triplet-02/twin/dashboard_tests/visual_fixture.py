"""Private subprocess fixture; control stdin is never an HTTP endpoint."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from city_dashboard.server import DashboardBridge
from city_live.runtime import LiveCity

city = LiveCity('browser-a', tick_budget=5000)
bridge = None
try:
    secret = sys.stdin.readline().rstrip('\n')
    city.sensor_fault('DTN-WLV-01', 'FALSE_READING', {'value': 180})
    for _ in range(10): city.step()
    bridge = DashboardBridge(city, secret, port=0)
    del secret
    bridge.start()
    print(json.dumps({'url': bridge.url}), flush=True)
    for line in sys.stdin:
        command = json.loads(line)
        if command['op'] == 'quit': break
        if command['op'] == 'reset': city.reset(command['run_id'])
        elif command['op'] == 'incident': city.incident(command['kind'], command.get('params', {}))
        elif command['op'] == 'frame':
            print(json.dumps(city.capture_dashboard_frame()), flush=True)
            continue
        elif command['op'] == 'advance':
            for _ in range(min(3000, max(0, int(command['ticks'])))): city.step()
        elif command['op'] == 'step': city.step()
        elif command['op'] == 'run':
            city.set_speed(10)
            city.start()
        print(json.dumps({'ack': True}), flush=True)
finally:
    if bridge is not None: bridge.close()
    else: city.close()
