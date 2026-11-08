from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import math
import sys
from types import MappingProxyType
import unittest
from unittest.mock import patch

from city_core import baseline_config, build_city
from tests.support import city
from tests.traffic_support import effect, reserve, snapshot, step
from tests.effect_support import request


def air_city(*, concentration=10.0, source=0.0, plant=True):
    config = baseline_config()
    for air in config['initial_physical']['air'].values():
        air.update(pm25_ug_m3=concentration,external_source_ug_m3_s=source)
    config['initial_physical']['feeders']['F-IND']['effective_closed'] = plant
    return city(initial_config=config)


class AirTests(unittest.TestCase):
    def test_independent_one_step_source_units_background_and_extreme(self):
        # Omitting dt, decay/background or supply gating breaks these Decimal oracles.
        for c, source, powered in ((10,0,True),(10,0,False),(0,0,False),
                                  (0,100,True),(1000,100,True),(1000,0,False)):
            with self.subTest(c=c,source=source,powered=powered):
                core = air_city(concentration=c,source=source,plant=powered)
                before = snapshot(core)
                after = step(core)
                for zone, air in after.physical_state.air.items():
                    plant = { 'Z-INDUSTRIAL':Decimal('.2'),'Z-RESIDENTIAL':Decimal('.05')}.get(zone,Decimal(0)) if powered else Decimal(0)
                    expected = Decimal(10)+(Decimal(c)-10)/Decimal('1.01')+Decimal(source)+plant
                    self.assertAlmostEqual(air.pm25_ug_m3,float(expected),places=10)
                    self.assertEqual(air.external_source_ug_m3_s,source)
                    self.assertEqual(before.physical_state.air[zone].pm25_ug_m3,c)
                self.assertEqual(after.context.sim_time_ms,1000)

    def test_multi_step_trajectory_and_source_off_relaxes_toward_background(self):
        core = air_city(concentration=1000,source=2,plant=False)
        expected = Decimal(1000)
        for _ in range(20):
            expected = 10+(expected-10)/Decimal('1.01')+2
            after = step(core)
            self.assertAlmostEqual(after.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,float(expected),places=10)
        held = after
        effect(core,'air_driver',source='B_SCENARIO',target_id='Z-INDUSTRIAL',source_ug_m3_s=0)
        self.assertEqual(snapshot(core).physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,held.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3)
        for _ in range(20):
            expected = 10+(expected-10)/Decimal('1.01')
            value = step(core).physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3
            self.assertAlmostEqual(value,float(expected),places=10)
            self.assertGreater(value,10)
        self.assertGreater(held.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,value)

    def test_actual_industrial_switch_stops_only_future_plant_source(self):
        core = air_city()
        first = step(core)
        self.assertEqual(first.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,10.2)
        effect(core,'feeder_position',target_id='F-IND',closed=False)
        before = snapshot(core)
        self.assertEqual(before.physical_state.air,first.physical_state.air)
        self.assertFalse(before.power.plant_active)
        self.assertTrue(before.power.hospital_powered)
        after = step(core)
        self.assertAlmostEqual(after.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,10+(.2/1.01))
        self.assertAlmostEqual(after.physical_state.air['Z-RESIDENTIAL'].pm25_ug_m3,10+(.05/1.01))
        self.assertGreater(after.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,10)
        self.assertTrue(after.power.hospital_powered)

    def test_dispatch_does_not_stop_emissions_or_invent_warning_policy(self):
        core = air_city()
        reserve(core,unit='POL-01')
        after = step(core)
        self.assertEqual(after.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,10.2)
        self.assertEqual(after.physical_state.responders['POL-01'].edge_remaining_s,49)
        self.assertTrue(after.power.plant_active)
        before = snapshot(core)
        self.assertFalse(core.actuators.apply_effect(request('air_warning',revision=before.context.revision,
            effect_id='warning',target_id='Z-INDUSTRIAL',value='danger')).ok)
        self.assertEqual(snapshot(core),before)

    def test_nonnegative_relaxation_and_largest_finite_concentrations(self):
        for concentration in (0.0,math.nextafter(0,1),1.0,10.0,1000.0,sys.float_info.max):
            with self.subTest(concentration=concentration):
                core = air_city(concentration=concentration,plant=False)
                after = step(core).physical_state.air['Z-DOWNTOWN'].pm25_ug_m3
                self.assertTrue(math.isfinite(after))
                self.assertGreaterEqual(after,0)
                if concentration < 10: self.assertLessEqual(concentration,after)
                if concentration > 10: self.assertLess(after,concentration)
                if concentration == 10: self.assertEqual(after,10)

    def test_invalid_concentration_source_unit_and_primary_override_reject_atomically(self):
        for field in ('pm25_ug_m3','external_source_ug_m3_s'):
            for value in (-1,True,'10',None,math.nan,math.inf,-math.inf):
                config = baseline_config()
                config['initial_physical']['air']['Z-INDUSTRIAL'][field] = value
                self.assertEqual(build_city('bad',initial_config=config).error.code,'INVALID_ARGUMENT')
        config=baseline_config()
        config['initial_physical']['air']['Z-INDUSTRIAL']['external_source_ug_m3_s']=100.0001
        self.assertEqual(build_city('bad',initial_config=config).error.code,'INVALID_ARGUMENT')
        core=air_city()
        before=snapshot(core)
        for value in (-1,100.0001,True,None,math.nan,math.inf):
            data=request('air_driver',source='B_SCENARIO',target_id='Z-INDUSTRIAL',source_ug_m3_s=value)
            self.assertEqual(core.scenarios.apply_effect(data).error.code,'INVALID_ARGUMENT')
            self.assertEqual(snapshot(core),before)
        for extra in ({'unit':'mg/m3'},{'pm25_ug_m3':0},{'reported_value':0},{'trusted':False}):
            data=request('air_driver',source='B_SCENARIO',target_id='Z-INDUSTRIAL',source_ug_m3_s=0,**extra)
            self.assertEqual(core.scenarios.apply_effect(data).error.code,'INVALID_ARGUMENT')
            self.assertEqual(snapshot(core),before)
        with self.assertRaises(TypeError): before.physical_state.air['Z-INDUSTRIAL']=None
        with self.assertRaises(FrozenInstanceError): before.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3=0

    def test_air_stage_numeric_failure_rolls_back_every_domain_and_duplicate_ledger(self):
        core=air_city()
        data=reserve(core)
        before=snapshot(core)
        trace=core.lifecycle.read_private_transitions('r-test',-1,100).value
        # Fault the last pure stage, after water/queues/travel have been computed.
        from city_core.validation import InvalidInput
        with patch('city_core.lifecycle.advance_air',side_effect=InvalidInput('MODEL_LIMIT')):
            result=core.lifecycle.advance_one('r-test',before.context.revision)
        self.assertEqual(result.error.code,'MODEL_LIMIT')
        self.assertEqual(snapshot(core),before)
        self.assertEqual(core.lifecycle.read_private_transitions('r-test',-1,100).value,trace)
        self.assertEqual(core.metadata.read_metadata().value.context,before.context)
        self.assertTrue(core.actuators.apply_effect(data).value.duplicate)
        recovered=step(core)
        self.assertEqual(recovered.context.tick,1)
        self.assertEqual(recovered.physical_state.responders['AMB-01'].edge_remaining_s,49)
        self.assertEqual(recovered.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3,10.2)

    def test_pure_numeric_guard_rejects_nonfinite_internal_result(self):
        from city_core.air import advance_air
        from city_core.validation import InvalidInput
        core=air_city()
        physical=snapshot(core).physical_state
        for value in (math.inf,math.nan,-1000.0):
            invalid=replace(physical,air=MappingProxyType({**physical.air,
                'Z-INDUSTRIAL':replace(physical.air['Z-INDUSTRIAL'],pm25_ug_m3=value)}))
            with self.assertRaises(InvalidInput) as raised:
                advance_air(invalid,baseline_config()['parameters'])
            self.assertEqual(raised.exception.code,'MODEL_LIMIT')
        self.assertEqual(snapshot(core).physical_state,physical)
