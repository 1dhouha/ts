"""Accelerated finite-run measurement; distinguishes bounded buffers from core history."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from city_live import LiveCity, LiveError


def run(ticks):
    tracemalloc.start()
    wall, cpu = time.perf_counter(), time.process_time()
    city = LiveCity('soak', seed=341, history_limit=100, tick_budget=ticks)
    counts, last = Counter(), 0
    try:
        city.traffic('time_of_day')
        city.run_scenario('combined_crisis')
        for _ in range(ticks):
            city.step()
            packet = city.public.events_since('soak', last)
            for row in packet['records']:
                counts[row['sensor_id']] += 1
            last = packet['next_after_seq']
        actual = city.status()['sim_tick']
        assert actual == ticks
        for sensor in city.sensors():
            assert counts[sensor['sensor_id']] == ticks // sensor['interval']
        try:
            city.step()
        except LiveError as error:
            assert error.code == 'BUDGET_EXHAUSTED'
        else:
            raise AssertionError('budget did not stop')
        current, peak = tracemalloc.get_traced_memory()
        return {'visibility': 'PRIVATE_OPERATOR_EVALUATOR_ONLY', 'ticks': actual,
                'wall_seconds': time.perf_counter() - wall, 'cpu_seconds': time.process_time() - cpu,
                'tracemalloc_current_bytes': current, 'tracemalloc_peak_bytes': peak,
                'sample_counts': dict(sorted(counts.items())), 'public_sequence': last,
                'retained_public_records': len(city.public.observations()),
                'retained_private_events': len(city.events()['events']),
                'core_records': city.status()['revision'], 'effect_count': city._effect_counter,
                'max_sensor_buffer': max(len(buffer) for buffer in city._sensors.buffers.values()),
                'budget_state': city.status()['state'], 'passed': True,
                'limits': 'Observed finite local run; core ledger/trace retain all commits until reset; not unbounded operation'}
    finally:
        city.close()
        tracemalloc.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ticks', type=int, default=3600)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.ticks)
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence / 'soak_private.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
