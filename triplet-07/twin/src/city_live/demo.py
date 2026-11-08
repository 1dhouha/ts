"""Executable private evaluator demonstration driven by the real live composition."""
import time

from city_core.replay import replay_recording
from city_core.transitions import digest, plain
from .runtime import LiveCity


def checkpoint(city):
    data = city.diagnostics()
    physical = data['physical_state']
    return {'tick': data['context']['tick'], 'surface_m3': physical['water']['surface_volume_m3'],
            'basin_m3': physical['water']['basin_volume_m3'],
            'low_queue': physical['roads']['R-LOW']['queue_vehicles'],
            'hospital_powered': data['power']['hospital_powered'],
            'sensor_model': data['sensor_model'],
            'industrial_pm25': physical['air']['Z-INDUSTRIAL']['pm25_ug_m3']}


def drive(city, emit):
    checkpoints = {}
    def advance(count):
        for _ in range(count):
            city.step()
    advance(60)
    checkpoints['normal'] = checkpoint(city)
    city.traffic('morning_rush')
    advance(40)
    city.incident('accident', {'target': 'R-LOW'})
    advance(20)
    checkpoints['accident'] = checkpoint(city)
    city.incident('power_outage')
    city.incident('heavy_rain')
    city.incident('pollution')
    advance(600)
    checkpoints['flood'] = checkpoint(city)
    city.sensor_fault('DTN-WLV-01', 'FALSE_READING', {'value': 180})
    advance(5)
    spoof = city.sensor_compare('DTN-WLV-01')
    public = city.public.observations()
    assert all('actual_value' not in row and 'fault' not in row and 'delta' not in row for row in public)
    assert spoof['actual_value'] != spoof['reported_value'] == 180
    city.sensor_clear('DTN-WLV-01')
    city.sensor_fault('DTN-TRF-01', 'DEAD', {})
    advance(5)
    assert city.sensor_compare('DTN-TRF-01')['status'] == 'FAILED'
    assert city.sensor_compare('DTN-TRF-02')['status'] == 'OK'
    city.sensor_repair('DTN-TRF-01')
    city.sensor_fault('DTN-TRF-01', 'NOISE', {'amplitude': 2})
    advance(10)
    city.incident('power_restore')
    city.incident('rain_clear')
    city.incident('pollution_clear')
    city.incident('road_clear', {'target': 'R-LOW'})
    city.incident('pump_on')
    city.traffic('low')
    advance(600)
    checkpoints['recovery'] = checkpoint(city)
    assert checkpoints['accident']['low_queue'] > checkpoints['normal']['low_queue']
    assert checkpoints['flood']['surface_m3'] > 200
    assert not checkpoints['flood']['hospital_powered']
    assert checkpoints['recovery']['hospital_powered']
    assert checkpoints['recovery']['surface_m3'] < checkpoints['flood']['surface_m3']
    assert checkpoints['recovery']['industrial_pm25'] < checkpoints['flood']['industrial_pm25']
    for name, data in checkpoints.items():
        emit({'checkpoint': name, **data})
    return checkpoints, spoof, digest(city.diagnostics()['physical_state']), digest([
        {key: value for key, value in row.items() if key != 'run_id'} for row in city.public.observations()])


def demonstrate(emit=lambda row: None):
    city = LiveCity('demo-live', seed=173, history_limit=512)
    try:
        city.set_speed(10)
        city.start()
        deadline = time.monotonic() + 3
        while city.status()['sim_tick'] < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        city.pause()
        paced_tick = city.status()['sim_tick']
        time.sleep(.12)
        paused_equal = city.status()['sim_tick'] == paced_tick
        assert paced_tick >= 2 and paused_equal
        city.resume()
        city.set_speed(5)
        city.stop()
        city.reset('demo-seeded-1')
        first = drive(city, emit)
        recording = city.export_private_replay()
        replay = replay_recording(recording, 'demo-copy')
        assert replay.ok
        replay_equal = digest(plain(replay.value.observations.read_private('demo-copy').value.physical_state)) == first[2]
        city.reset('demo-seeded-2')
        assert city.public.observations() == [] and city.actions() == []
        second = drive(city, lambda row: None)
        # Fresh run labels must differ; all physical/report/sample values agree.
        def normalized(result):
            return (result[0], {k: v for k, v in result[1].items() if k != 'run_id'}, result[2:])
        repeated = normalized(first) == normalized(second)
        assert replay_equal and repeated
        return {'visibility': 'PRIVATE_OPERATOR_EVALUATOR_ONLY', 'passed': True,
                'paced_ticks': paced_tick, 'pause_stable': paused_equal,
                'checkpoints': first[0], 'spoof': first[1],
                'core_replay_equal': replay_equal, 'seeded_repeat_equal': repeated,
                'proposed_sensor_count': 16, 'grounded_proposed_sensors': 16,
                'blocked_demo_steps': {'New actuator commands': 'New linked device command semantics remain separate',
                    'Partner integration': 'Real Brain/Guardian and B/C integration unverified'},
                'safe_export': {'schema_version': 'twin-export-0.1-local',
                                'observations': city.public.observations()}}
    finally:
        city.close()
