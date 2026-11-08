"""Seeded aggregate demand; no queue setters or alternative road physics."""
import hashlib
import math

from city_core.transitions import freeze
from .records import LiveError, exact_keys, numeric

ROADS = ('R-DOWN', 'R-HIGH', 'R-IND', 'R-LINK', 'R-LOW')
PROFILES = freeze({'normal': .2, 'morning_rush': .85, 'evening_rush': .75,
                   'low': .05, 'surge': 1.5, 'congestion': 1.0})


def jitter(seed, road, tick):
    raw = hashlib.sha256(f'{seed}|traffic|{road}|{tick}'.encode()).digest()
    return int.from_bytes(raw[:8], 'big') / (2**64 - 1) * 2 - 1


def profile_config(value):
    exact_keys(value, ('base', 'amplitude', 'transition_ticks'))
    from .records import integer
    return freeze({'base': numeric(value['base'], 0, 1.9),
                   'amplitude': numeric(value['amplitude'], 0, .1),
                   'transition_ticks': integer(value['transition_ticks'], 1, 600)})


class TrafficDemand:
    def __init__(self, seed):
        self.seed = seed
        self.profile = 'normal'
        self.started = 0
        self.origins = freeze({road: .2 for road in ROADS})
        self.config = profile_config({'base': .2, 'amplitude': .015, 'transition_ticks': 20})
        self.last_update = -10

    def select(self, name, tick, roads, config=None):
        if name not in (*PROFILES, 'time_of_day', 'custom'):
            raise LiveError('INVALID_PROFILE')
        selected = profile_config(config) if name == 'custom' else profile_config({
            'base': PROFILES.get(name, .2), 'amplitude': .015, 'transition_ticks': 20})
        self.profile, self.started, self.config = name, tick, selected
        self.origins = freeze({road: roads[road].arrival_rate_veh_s for road in ROADS})
        self.last_update = -10

    def rates(self, tick, epoch_hour):
        # Update every 10 simulated seconds, conserving finite core ledger budget.
        if tick - self.last_update < 10:
            return {}
        target = self.config['base']
        if self.profile == 'time_of_day':
            hour = (epoch_hour + tick / 3600) % 24
            target = .08 + .8 * math.exp(-((hour - 8) / 1.5)**2) + .7 * math.exp(-((hour - 18) / 2)**2)
        progress = min(1, (tick - self.started) / self.config['transition_ticks'])
        result = {road: max(0, min(2, self.origins[road] + (target - self.origins[road]) * progress
                      + self.config['amplitude'] * jitter(self.seed, road, tick))) for road in ROADS}
        self.last_update = tick
        return result
