from dataclasses import FrozenInstanceError
import math
import unittest

from city_core import baseline_config, baseline_catalog
from city_core.transitions import plain
from tests.traffic_support import traffic_city, snapshot, step, effect


class TrafficTests(unittest.TestCase):
    def test_queue_accounting_normal_priority_stop_and_unpowered(self):
        # A wrong capacity multiplier or dropping arrivals breaks these hand oracles.
        for changes, capacities in (
            ({}, {'R-LOW': .5, 'R-HIGH': .5, 'R-LINK': .5, 'R-DOWN': .5, 'R-IND': .5}),
            ({'modes': {'J-D1': 'priority'}}, {'R-LOW': .75, 'R-HIGH': .5, 'R-LINK': .5, 'R-DOWN': .5, 'R-IND': .75}),
            ({'modes': {'J-D2': 'stop'}}, {'R-LOW': .5, 'R-HIGH': .5, 'R-LINK': 0, 'R-DOWN': 0, 'R-IND': .5}),
            ({'urban': False}, {'R-LOW': 0, 'R-HIGH': .5, 'R-LINK': 0, 'R-DOWN': 0, 'R-IND': 0})):
            with self.subTest(changes=changes):
                core = traffic_city(**changes)
                before, after = snapshot(core), step(core)
                for road, capacity in capacities.items():
                    self.assertAlmostEqual(after.physical_state.roads[road].queue_vehicles, 10.2-capacity)
                    self.assertEqual(before.traffic[road].capacity_veh_s, capacity)
                    self.assertEqual(after.traffic[road].capacity_veh_s, capacity)
                self.assertEqual(before.physical_state.roads['R-LOW'].queue_vehicles, 10)

    def test_empty_queue_saturation_obstruction_and_no_silent_storage_cap(self):
        for queue, arrivals, obstruction, expected in (
            (0,0,0,0), (0,.2,0,0), (.1,.2,0,0), (.5,.2,0,.2),
            (10,2,.5,11.75), (10,2,1,12), (1e6,2,1,1000002)):
            with self.subTest(queue=queue, arrivals=arrivals, obstruction=obstruction):
                core = traffic_city(queue=queue, arrivals=arrivals, obstructions={'R-LOW': obstruction})
                after = step(core)
                self.assertAlmostEqual(after.physical_state.roads['R-LOW'].queue_vehicles, expected)
                self.assertEqual(after.traffic['R-LOW'].usable, obstruction < 1)

    def test_flood_closes_exactly_and_preserves_queue_on_reopening(self):
        for road, cutoff in (('R-LOW',200.0), ('R-HIGH',1000.0)):
            for surface, closed in ((math.nextafter(cutoff,0),False), (cutoff,True),
                                    (math.nextafter(cutoff,math.inf),True)):
                with self.subTest(road=road,surface=surface):
                    core = traffic_city(surface=surface)
                    self.assertEqual(snapshot(core).traffic[road].usable, not closed)
                    after = step(core)
                    self.assertAlmostEqual(after.physical_state.roads[road].queue_vehicles,
                                           10.2 if closed else 9.7)
        core = traffic_city(surface=200)
        blocked = step(core)
        effect(core,'pump_enable',target_id='P-03',enabled=True)
        reopened = step(core)
        self.assertTrue(reopened.traffic['R-LOW'].usable)
        self.assertAlmostEqual(reopened.physical_state.roads['R-LOW'].queue_vehicles, 9.9)
        self.assertAlmostEqual(blocked.physical_state.roads['R-LOW'].queue_vehicles, 10.2)

    def test_access_direct_alternative_disconnected_and_supply_separation(self):
        for changes, expected in (({},True), ({'surface':200},True),
            ({'surface':1000},False), ({'surface':200,'obstructions':{'R-DOWN':1}},False),
            ({'surface':200,'obstructions':{'R-LINK':1}},False),
            ({'obstructions':{'R-LOW':1,'R-HIGH':1}},False),
            ({'urban':False},False), ({'ess':False},True)):
            with self.subTest(changes=changes):
                core = traffic_city(**changes)
                access = snapshot(core).access
                self.assertEqual(access.facility_id,'HOSP-01')
                self.assertEqual(access.destination_node_id,'J-H1')
                self.assertEqual(access.reachable_by_origin['J-D1'],expected)
                self.assertTrue(access.reachable_by_origin['J-H1'])
                if changes.get('urban') is False:
                    self.assertTrue(access.reachable_by_origin['J-R1'])
                    self.assertFalse(access.reachable_by_origin['J-D2'])
                if changes.get('ess') is False:
                    self.assertFalse(snapshot(core).power.hospital_powered)
        core = traffic_city(queue=1e100)
        self.assertTrue(snapshot(core).access.reachable_by_origin['J-I1'])

    def test_access_and_conditions_are_immutable_private_derived_views(self):
        core = traffic_city()
        held = snapshot(core)
        with self.assertRaises(TypeError): held.traffic['R-LOW'] = None
        with self.assertRaises(FrozenInstanceError): held.traffic['R-LOW'].usable = False
        with self.assertRaises(TypeError): held.access.reachable_by_origin['J-D1'] = False
        with self.assertRaises(FrozenInstanceError): held.access.facility_id = 'other'
        effect(core,'signal_mode',target_id='J-D1',mode='stop')
        effect(core,'signal_mode',target_id='J-D2',mode='stop')
        self.assertFalse(snapshot(core).access.reachable_by_origin['J-D1'])
        self.assertTrue(held.access.reachable_by_origin['J-D1'])
        self.assertEqual(held.traffic['R-LOW'].capacity_veh_s,.5)
        self.assertNotIn('traffic',plain(held))
        self.assertNotIn('access',plain(held))

    def test_fixed_graph_and_capacity_profile_match_packaged_model(self):
        # This checks independent fixed constants against the accepted artifact.
        from city_core.access import ROAD_ENDPOINTS
        from city_core.traffic import TRAFFIC_PROFILE, ROAD_SIGNALS
        config = baseline_config()['parameters']
        for key, value in TRAFFIC_PROFILE.items(): self.assertEqual(value,config[key])
        catalog = baseline_catalog()
        for road, endpoints in ROAD_ENDPOINTS.items():
            self.assertEqual(endpoints, tuple(r['target_id'] for r in catalog['relationships']
                if r['source_id']==road and r['relation'] in ('endpoint_a','endpoint_b')))
        self.assertEqual(ROAD_SIGNALS,{'R-LOW':'J-D1','R-IND':'J-D1','R-DOWN':'J-D2','R-LINK':'J-D2'})
        self.assertEqual(set(ROAD_ENDPOINTS),set(config['road_length_m']))
