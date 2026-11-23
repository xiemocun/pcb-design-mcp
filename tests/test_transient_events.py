"""Analytic event times and real native solver waveform/contract checks."""
import copy
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from pcb_mcp import functional,registry,spice,transient_events


def terminal(ref,number='1'):return dict(ref=ref,number=number)
def trigger(**extra):
    return dict(positive=terminal('J1'),negative=terminal('J1','2'),threshold=.5,
                direction='rising',search_window_s=[0.,1.],**extra)
def delay():
    return functional.DelayMeasurement(trigger=trigger(),target_threshold=.5,
        target_direction='rising',observation_end_s=4.)
def settling():
    return functional.SettlingMeasurement(trigger=trigger(),band=[.9,1.1],
        minimum_hold_s=1.,observation_end_s=4.)
def requirement(quantity,**extra):
    return dict(id='timing',description='Explicit actual trace event timing requirement',
        positive=terminal('J2'),negative=terminal('J2','2'),quantity=quantity,
        minimum=0.,maximum=1.,scenarios=['case'],**extra)


class EventNumerics(unittest.TestCase):
    def test_delay_matches_independent_linear_crossings(self):
        r=transient_events.measure([0.,0.,1.,1.],[0.,1.,1.,1.],[0.,1.,2.,4.],delay())
        self.assertEqual(r['trigger_crossing_s'],.5)
        self.assertEqual(r['target_crossing_s'],1.5)
        self.assertEqual(r['seconds'],1.)

    def test_trigger_on_search_boundary_and_second_occurrence(self):
        edge=functional.EdgeTrigger(**dict(trigger(),search_window_s=[.5,2.5],occurrence=2))
        self.assertEqual(transient_events.trigger_time([0.,1.,0.,1.,0.],[0.,1.,2.,3.,4.],edge),2.5)

    def test_no_trigger_and_no_post_trigger_target_are_not_zero_delay(self):
        for signal,reference,code in [([0.,1.,1.],[0.,0.,0.],'TRIGGER_EDGE'),
                                      ([1.,1.,1.],[0.,1.,1.],'TARGET_EDGE')]:
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,code):
                transient_events.measure(signal,reference,[0.,1.,4.],delay())

    def test_falling_edge_and_occurrence_are_explicit(self):
        edge=functional.EdgeTrigger(**dict(trigger(),direction='falling'))
        self.assertEqual(transient_events.trigger_time([1.,0.,1.],[0.,1.,4.],edge),.5)
        with self.assertRaisesRegex(ValueError,'TRIGGER_EDGE'):
            transient_events.trigger_time([1.,0.,1.],[0.,1.,4.],edge.model_copy(update=dict(occurrence=2)))

    def test_late_ringing_defeats_first_entry_settling(self):
        # Initial entry at1s is followed by a late excursion; final entry is2.5s.
        r=transient_events.measure([0.,1.,1.2,1.,1.],[0.,1.,1.,1.,1.],
            [0.,1.,2.,3.,4.],settling())
        self.assertAlmostEqual(r['settled_at_s'],2.5)
        self.assertAlmostEqual(r['seconds'],2.)
        self.assertAlmostEqual(r['observed_hold_s'],1.5)

    def test_initially_in_band_is_zero_only_after_real_trigger(self):
        r=transient_events.measure([1.,1.,1.],[0.,1.,1.],[0.,1.,4.],settling())
        self.assertEqual(r['seconds'],0.)

    def test_not_reached_and_insufficient_hold_fail(self):
        for values,code in [([0.,0.,0.],'BAND_NOT_REACHED'),([0.,0.,1.],'HOLD_NOT_COVERED')]:
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,code):
                transient_events.measure(values,[0.,1.,1.],[0.,1.,4.],settling())

    def test_observation_cannot_extend_beyond_actual_samples(self):
        with self.assertRaisesRegex(ValueError,'WINDOW_NOT_COVERED'):
            transient_events.measure([0.,1.],[0.,1.],[0.,1.],delay())


class EventContracts(unittest.TestCase):
    def test_waveform_exclusivity_time_coverage_and_numeric_input(self):
        base=dict(positive=terminal('J1'),negative=terminal('J1','2'),dc_v=0.)
        for extra in [dict(pwl=[[0.,0.],[0.,1.]]),dict(pwl=[[-1.,0.],[1.,1.]]),
                      dict(pwl=[[0.,0.],[1.,float('nan')]]),
                      dict(pwl=[[0.,0.],[1.,1.]],pulse=[0.,1.,0.,.1,.1,.1,1.])]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):functional.Drive(**base,**extra)
        d=dict(base,pwl=[[0.,0.],[1.,1.]])
        for scenario,code in [(dict(engine='spice_op'),'WAVEFORM_REQUIRES'),
                              (dict(engine='spice_transient',stop_s=2.,step_s=.01),'PWL_COVERAGE')]:
            with self.assertRaisesRegex(ValueError,code):functional.Scenario(id='case',drives=[d],**scenario)

    def test_event_options_and_observation_are_not_silently_ignored(self):
        raw=requirement('delay',delay=delay().model_dump())
        functional.FunctionalRequirement.model_validate(raw)
        for change in [dict(aggregation='final'),dict(time_window_s=[0.,1.]),
                       dict(quantity='voltage'),dict(minimum=-1.)]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                functional.FunctionalRequirement.model_validate(dict(raw,**change))
        for engine,end,code in [('spice_op',None,'EVENT_REQUIRES'),('spice_transient',3.,'OBSERVATION_UNCOVERED')]:
            case=dict(id='case',engine=engine)
            if end:case.update(stop_s=end,step_s=.01)
            with self.assertRaisesRegex(ValueError,code):
                functional.FunctionalContract(description='Event coverage cannot be inferred',requirements=[raw],scenarios=[case])

    def test_settling_band_and_required_hold_are_explicit(self):
        for change in [dict(band=[1.,1.]),dict(minimum_hold_s=0.),dict(observation_end_s=1.5)]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                functional.SettlingMeasurement(**dict(settling().model_dump(),**change))


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native ngspice unavailable')
class NativeTransientEvents(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory()
    def tearDown(self):self.temp.cleanup()

    def test_real_voltage_and_current_sinusoids_match_closed_form(self):
        for kind,offset,amplitude in [('voltage',1.,.2),('current',.001,.0002)]:
            with self.subTest(kind=kind):
                d=functional.Drive(positive=terminal('J1'),negative=terminal('J1','2'),kind=kind,
                    **{('dc_v' if kind=='voltage' else 'dc_a'):offset},
                    sine=dict(offset=offset,amplitude=amplitude,frequency_hz=1000.,phase_deg=30.))
                deck='Sine stimulus\n'+'\n'.join(functional.drive_lines(d,0,'p','0'))+'\nR1 p 0 1000\n.tran 1u 3m 0 1u\n.end\n'
                _,waves=spice.run(deck,['time','v(p)'],Path(self.temp.name)/kind)
                expected=offset+amplitude*np.sin(2*np.pi*1000*waves['time']+np.pi/6)
                if kind=='current':expected=-1000*expected
                np.testing.assert_allclose(waves['v(p)'],expected,atol=1e-10)

    def test_real_current_pwl_preserves_amperes_and_polarity(self):
        d=functional.Drive(positive=terminal('J1'),negative=terminal('J1','2'),kind='current',dc_a=0.,pwl=[[0.,0.],[.001,.001],[.002,0.]])
        deck='Current ramp\n'+'\n'.join(functional.drive_lines(d,0,'p','0'))+'\nR1 p 0 1000\n.tran 1u 2m 0 1u\n.end\n'
        _,waves=spice.run(deck,['time','v(p)'],Path(self.temp.name)/'current_pwl')
        self.assertAlmostEqual(float(np.interp(.0005,waves['time'],waves['v(p)'])),-.5,places=8)
        self.assertAlmostEqual(float(np.interp(.001,waves['time'],waves['v(p)'])),-1.,places=8)

    def test_functional_rc_delay_and_settling_against_logarithmic_solution(self):
        state=dict(id='a'*32,revision=0,components={},nets={},pin_intents={});records={}
        for ref in ['J1','J2','R1']:
            connector=ref.startswith('J')
            state['components'][ref]=dict(id=ref,ref=ref,library_id='Connector_Generic:Conn_01x02' if connector else 'Device:R',value='Conn_01x02' if connector else '1k',pins=[dict(pin_id=n,number=n) for n in ['1','2']])
            records[ref]=dict(kind='connector' if connector else 'resistor',definition_hash='b'*64,facts={} if connector else dict(value_ohm=1000.,rated_power_w=.1,voltage_rating_v=75.))
        for nid,pairs in [('input',[('J1','1'),('R1','1')]),('output',[('R1','2'),('J2','1')]),('ground',[('J1','2'),('J2','2')])]:
            state['nets'][nid]=dict(id=nid,pins=[dict(component_id=r,pin_id=n) for r,n in pairs])
        edge=dict(trigger(),search_window_s=[.5e-6,2e-6])
        reqs=[dict(requirement('delay',delay=dict(trigger=edge,target_threshold=.5,target_direction='rising',observation_end_s=.005)),minimum=.00069,maximum=.00070),
              dict(requirement('settling_time',settling=dict(trigger=edge,band=[.95,1.05],minimum_hold_s=.001,observation_end_s=.005)),id='settling',minimum=.00299,maximum=.00300)]
        scenario=dict(id='case',engine='spice_transient',stop_s=.005,step_s=1e-6,
            drives=[dict(positive=terminal('J1'),negative=terminal('J1','2'),dc_v=0.,pwl=[[0.,0.],[1e-6,0.],[1.1e-6,1.],[.005,1.]])],
            boundary_loads=[dict(positive=terminal('J2'),negative=terminal('J2','2'),capacitance_f=1e-6)])
        contract=functional.FunctionalContract(description='Real native RC transient event validation',requirements=reqs,scenarios=[scenario])
        request=SimpleNamespace(contract=contract,contract_sha256=registry.sha(registry.canonical(contract.model_dump(mode='json'))),model_bindings={})
        folder=Path(self.temp.name)/'contract';folder.mkdir()
        with patch('pcb_mcp.functional.resolved_parts',return_value=records):result=functional.evaluate(state,request,folder)
        self.assertEqual(result['verdict'],'pass',result['findings'])
        rows={x['requirement']:x for x in result['measurements']}
        self.assertAlmostEqual(rows['timing']['minimum'],math.log(2)*.001,delta=2e-7)
        self.assertAlmostEqual(rows['settling']['minimum'],math.log(20)*.001,delta=2e-7)
        # Changing only the actual requirement must fail, not rewrite a deadline.
        request.contract=contract.model_copy(deep=True);request.contract.requirements[1].maximum=.0025
        request.contract_sha256=registry.sha(registry.canonical(request.contract.model_dump(mode='json')))
        folder=Path(self.temp.name)/'late';folder.mkdir()
        with patch('pcb_mcp.functional.resolved_parts',return_value=records):bad=functional.evaluate(state,request,folder)
        self.assertEqual(bad['verdict'],'fail');self.assertFalse(bad['measurements'][1]['passed'])


if __name__=='__main__':unittest.main()
