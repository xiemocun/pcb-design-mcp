"""Actual solver port signs, fanout and energy against independent equations."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from pcb_mcp import functional,registry,spice,port_currents


def fixture():
    state=dict(id='a'*32,revision=0,components={},nets={},pin_intents={})
    records={}
    for ref in ['J1','J2','J3','R1']:
        connector=ref.startswith('J')
        state['components'][ref]=dict(id=ref,ref=ref,library_id='Connector_Generic:Conn_01x02' if connector else 'Device:R',value='Conn_01x02' if connector else '1k',pins=[dict(pin_id=n,number=n) for n in ['1','2']])
        records[ref]=dict(kind='connector' if connector else 'resistor',definition_hash='b'*64,facts={} if connector else dict(value_ohm=1000.,rated_power_w=.1,voltage_rating_v=75.))
    for nid,pairs in [('input',[('J1','1'),('R1','1')]),('output',[('R1','2'),('J2','1'),('J3','1')]),('ground',[('J1','2'),('J2','2'),('J3','2')])]:
        state['nets'][nid]=dict(id=nid,pins=[dict(component_id=r,pin_id=n) for r,n in pairs])
    return state,records


def terminal(ref,n='1'):return dict(ref=ref,number=n)
def requirement(key,ref,quantity,low,high,**extra):
    return dict(id=key,description='Synthetic external port reference, no manufacturer qualification',positive=terminal(ref),negative=terminal(ref,'2'),quantity=quantity,minimum=low,maximum=high,scenarios=['case'],measurement_scope='external_port',**extra)


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native ngspice unavailable')
class ExternalPortTests(unittest.TestCase):
    def install_board_capacitor(self):
        self.state['components']['C1']=dict(id='C1',ref='C1',library_id='Device:C',value='1uF',
            pins=[dict(pin_id=n,number=n) for n in ('1','2')])
        self.records['C1']=dict(kind='capacitor',definition_hash='c'*64,facts=dict(capacitance_f=1e-6,voltage_rating_v=16.))
        for nid,pin in [('output','1'),('ground','2')]:
            self.state['nets'][nid]['pins'].append(dict(component_id='C1',pin_id=pin))
        self.scenario.update(engine='spice_transient',step_s=1e-6,stop_s=.002,boundary_loads=[],
            capacitor_initial_voltages=[dict(terminal=terminal('C1','2'),voltage_v=-3.)])
        self.scenario['drives'][0]['dc_v']=0.

    def test_board_capacitor_discharges_through_existing_resistor(self):
        self.install_board_capacitor()
        # 3 V initially discharges through the real 1k and 1 uF, tau=1 ms.
        r=requirement('decay','J2','voltage',1.09,1.12,time_window_s=[.00099,.00101])
        r['measurement_scope']='component'
        result=self.evaluate([r]);self.assertEqual(result['verdict'],'pass',result['findings'])
        run=result['runs'][0];initial=next(m['initial_state'] for m in run['models'] if m['reference']=='C1')
        self.assertAlmostEqual(initial['solved_initial_node_v'],3.,places=8)
        deck=(Path(run['artifact_directory'])/'circuit.cir').read_text()
        self.assertNotIn('Cfixture',deck);self.assertEqual(deck.count('\n.ic '),1)

    def test_board_initial_state_rejects_wrong_part_pin_floating_and_conflicting_fixture(self):
        self.install_board_capacitor()
        def compile_case():
            return functional.compile_spice(self.state,functional.Scenario(**self.scenario),{},self.records)
        item=self.scenario['capacitor_initial_voltages'][0]
        item['terminal']=terminal('R1')
        with self.assertRaisesRegex(ValueError,'REQUIRES_BOUND_CAPACITOR'):compile_case()
        item['terminal']=terminal('C1','3')
        with self.assertRaisesRegex(ValueError,'INITIAL_PIN_INVALID'):compile_case()
        item['terminal']=terminal('C1')
        self.scenario['boundary_loads']=[dict(positive=terminal('J2'),negative=terminal('J2','2'),capacitance_f=1e-9,initial_v=0.)]
        with self.assertRaisesRegex(ValueError,'VOLTAGE_CONFLICT'):compile_case()
        self.scenario['boundary_loads']=[]
        self.state['nets']['input']['pins'].append(self.state['nets']['ground']['pins'].pop())
        with self.assertRaisesRegex(ValueError,'REQUIRES_GROUNDED_CAPACITOR'):compile_case()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.ctx=registry.using(str(Path(self.temp.name)/'data'));self.ctx.__enter__()
        self.state,self.records=fixture()
        self.scenario=dict(id='case',engine='spice_op',drives=[dict(positive=terminal('J1'),negative=terminal('J1','2'),dc_v=5.)],boundary_loads=[dict(positive=terminal('J2'),negative=terminal('J2','2'),resistance_ohm=1000.),dict(positive=terminal('J3'),negative=terminal('J3','2'),resistance_ohm=2000.)])
    def tearDown(self):self.ctx.__exit__(None,None,None);self.temp.cleanup()
    def evaluate(self,requirements):
        contract=functional.FunctionalContract.model_validate(dict(description='Synthetic native solver port test',requirements=requirements,scenarios=[self.scenario,*getattr(self,'extra_scenarios',[])]))
        request=SimpleNamespace(contract=contract,contract_sha256=registry.sha(registry.canonical(contract.model_dump(mode='json'))),model_bindings={})
        folder=Path(self.temp.name)/'solver';folder.mkdir()
        with patch('pcb_mcp.functional.resolved_parts',return_value=self.records):return functional.evaluate(self.state,request,folder)
    def test_shared_net_ports_have_distinct_currents_and_energy_balance(self):
        expected=[('input_i','J1','current',-.003),('load_i','J2','current',.002),('other_i','J3','current',.001),('input_p','J1','power',-.015),('load_p','J2','power',.004),('other_p','J3','power',.002)]
        req=[requirement(k,r,q,v-1e-9,v+1e-9) for k,r,q,v in expected]
        req.append(dict(requirement('resistor_p','R1','power',.009-1e-9,.009+1e-9),measurement_scope='component'))
        result=self.evaluate(req);self.assertEqual(result['verdict'],'pass',result['findings'])
        values={m['requirement']:m['minimum'] for m in result['measurements']}
        self.assertAlmostEqual(values['input_p']+values['load_p']+values['other_p']+values['resistor_p'],0.,places=12)
        self.assertEqual(len(result['runs'][0]['electrical_reference']['component_stress']),1)
    def test_ac_port_components_against_complex_admittance(self):
        self.scenario.update(engine='spice_ac',frequency_hz=[100.,1000.],points=3)
        self.scenario['drives'][0].update(dc_v=0.,ac_v=1.)
        self.scenario['boundary_loads'][0].pop('resistance_ohm');self.scenario['boundary_loads'][0]['capacitance_f']=1e-6
        y=1/2000+2j*np.pi*np.linspace(100,1000,3)*1e-6;expected=2j*np.pi*np.linspace(100,1000,3)*1e-6/(1+1000*y)
        result=self.evaluate([requirement('real','J2','current',-1.,1.,ac_measurement='real'),requirement('imag','J2','current',-1.,1.,ac_measurement='imaginary')])
        self.assertEqual(result['verdict'],'pass',result['findings'])
        for row,values in zip(result['measurements'],[expected.real,expected.imag]):
            self.assertAlmostEqual(row['minimum'],float(values.min()),places=12);self.assertAlmostEqual(row['maximum'],float(values.max()),places=12)
    def test_bandlimited_differential_ripple_matches_independent_transfer_functions(self):
        self.scenario.update(engine='spice_transient',step_s=5e-7,stop_s=.006)
        self.scenario['drives'][0].update(sine=dict(offset=5.,amplitude=.2,frequency_hz=4000.))
        # R1 feeds a 1k||2k load: the voltage drop is 60% of the input.
        # DC cancels in peak-to-peak, and a 4kHz tone sees the two distinct roll-offs.
        def observation(key,expected,response=None):
            row=dict(requirement(key,'J1','voltage',expected*.999,expected*1.001,
                                 time_window_s=[.004,.006],aggregation='peak_to_peak'),
                     negative=terminal('J2'),measurement_scope='component')
            if response is not None:row['lowpass']=dict(response=response,cutoff_hz=1000.)
            return row
        rows=[observation('unfiltered',.24),observation('single',.24/np.sqrt(1+4**2),'single_pole'),
              observation('butterworth',.24/np.sqrt(1+4**4),'butterworth_2')]
        failure=dict(rows[-1],id='too_much_ripple',minimum=0.,maximum=.014)
        result=self.evaluate([*rows,failure])
        self.assertEqual(result['verdict'],'fail')
        self.assertEqual([r['passed'] for r in result['measurements']],[True,True,True,False],result['findings'])
        self.assertEqual(result['measurements'][2]['lowpass_evidence'],result['measurements'][3]['lowpass_evidence'])
        deck=(Path(result['runs'][0]['artifact_directory'])/'circuit.cir').read_text()
        self.assertEqual(deck.count('\nEmcp_meter_'),2)

    def test_spectral_noise_measures_real_differential_resistor_noise_and_dc_bias(self):
        from math import sqrt
        bias=copy.deepcopy(self.scenario);bias['id']='bias'
        self.scenario.update(engine='spice_noise',frequency_hz=[20.,20000.],points=101)
        self.scenario['drives'][0]['ac_v']=1.
        self.extra_scenarios=[bias]
        # The output is shunted by 1k, 1k and 2k, each with thermal noise.
        rms=sqrt(4*1.380649e-23*298.15*400*(20000-20))
        noise=dict(requirement('noise','J1','noise_voltage_rms',.999*rms,1.001*rms),negative=terminal('J2'),measurement_scope='component')
        op=dict(requirement('bias_v','J2','voltage',1.999,2.001),scenarios=['bias'],measurement_scope='component')
        result=self.evaluate([noise,op,dict(noise,id='excess_noise',minimum=0.,maximum=rms*.99)])
        self.assertEqual(result['verdict'],'fail')
        self.assertEqual([r['passed'] for r in result['measurements']],[True,False,True],result['findings'])
        self.assertEqual(result['runs'][0]['electrical_reference']['sources'][0]['analysis_scope'],'matching_dc_bias_for_small_signal_noise')
        self.assertEqual(result['runs'][0]['noise_reference']['frequency_points'],101)

    def test_capacitor_initial_state_and_port_current_follow_closed_form(self):
        self.scenario.update(engine='spice_transient',step_s=1e-7,stop_s=.001)
        self.scenario['boundary_loads'][0].pop('resistance_ohm');self.scenario['boundary_loads'][0].update(capacitance_f=1e-6,initial_v=1.)
        # Rth=1k||2k, Vfinal=5*2/3; C*dV/dt at 0.2ms.
        expected=(5*2/3-1)/(1000*2000/3000)*np.exp(-.0002/((1000*2000/3000)*1e-6))
        result=self.evaluate([requirement('cap_i','J2','current',float(expected)*.999,float(expected)*1.001,time_window_s=[.0002,.0002])])
        self.assertEqual(result['verdict'],'pass',result['findings'])
    def test_rl_port_current_matches_step_response(self):
        self.scenario.update(engine='spice_transient',step_s=1e-7,stop_s=.001)
        self.scenario['drives'][0]['pulse']=[0.,5.,.0001,1e-9,1e-9,.002,.004]
        self.scenario['boundary_loads'][0]['inductance_h']=1.
        # Seen by the RL load: Vth=10/3 V, Rth=2k/3 ohm.
        expected=(10/3)/(1000+2000/3)*(1-np.exp(-.0002*(1000+2000/3)))
        result=self.evaluate([requirement('rl_i','J2','current',expected*.999,expected*1.001,time_window_s=[.0003,.0003])])
        self.assertEqual(result['verdict'],'pass',result['findings'])
    def test_compliant_current_sink_matches_piecewise_circuit_equations(self):
        self.scenario.update(engine='spice_transient',step_s=1e-6,stop_s=.005)
        self.scenario['drives'][0].update(dc_v=-1.,pwl=[[0,-1],[.001,-1],[.00101,1],[.002,1],[.00201,5],[.005,5]])
        sink=self.scenario['boundary_loads'][0]
        del sink['resistance_ohm']
        sink['current_sink']=dict(current_a=.002,compliance_v=1.)
        # R1 is 1k, J3 is 2k, and the load ramp below compliance is 2mS.
        expected=[('reverse',.0005,-2/3,0.),('ramp',.0015,2/7,.002*2/7),('limited',.003,2.,.002)]
        req=[]
        for key,t,volts,amps in expected:
            window=dict(time_window_s=[t,t])
            req.append(dict(requirement(key+'_v','J2','voltage',volts-1e-8,volts+1e-8,**window),
                            measurement_scope='component'))
            req.append(requirement(key+'_i','J2','current',amps-1e-10,amps+1e-10,**window))
        req.append(requirement('limited_source','J1','current',-.003-1e-10,-.003+1e-10,time_window_s=[.003,.003]))
        result=self.evaluate(req)
        self.assertEqual(result['verdict'],'pass',result['findings'])
        self.assertEqual(len(result['measurements']),7)

    def test_compliant_sink_rejects_conflicting_load_or_ac(self):
        load=dict(self.scenario['boundary_loads'][0],current_sink=dict(current_a=.002,compliance_v=1.))
        with self.assertRaisesRegex(ValueError,'ONE_BOUNDARY_LOAD_KIND_REQUIRED'):
            functional.BoundaryLoad.model_validate(load)
        del load['resistance_ohm']
        ac=dict(self.scenario,engine='spice_ac',frequency_hz=[100.,1000.],boundary_loads=[load],
                drives=[dict(self.scenario['drives'][0],ac_v=1.)])
        with self.assertRaisesRegex(ValueError,'CURRENT_SINK_LOAD_REQUIRES_SPICE_DC_OR_TRANSIENT'):
            functional.Scenario.model_validate(ac)

    def test_native_passive_current_keeps_both_terminal_signs_and_power(self):
        self.state['components']['C1']=dict(id='C1',ref='C1',library_id='Device:C',value='1u',
                                            pins=[dict(pin_id=n,number=n) for n in ('1','2')])
        self.records['C1']=dict(kind='capacitor',definition_hash='c'*64,facts=dict(capacitance_f=1e-6,voltage_rating_v=50.))
        self.state['nets']['output']['pins'].append(dict(component_id='C1',pin_id='1'))
        self.state['nets']['ground']['pins'].append(dict(component_id='C1',pin_id='2'))
        self.scenario.update(engine='spice_transient',step_s=1e-6,stop_s=.001,boundary_loads=[],passive_current_observation='native')
        self.scenario['drives'][0]['pulse']=[0.,5.,.0001,1e-9,1e-9,.002,.004]
        current=float(.005*np.exp(-.0002/.001))
        def measured(key,ref,quantity,expected,reverse=False):
            row=requirement(key,ref,quantity,expected-abs(expected)*.001,expected+abs(expected)*.001,
                            time_window_s=[.0003,.0003])
            row['measurement_scope']='component'
            if reverse:row['positive'],row['negative']=row['negative'],row['positive']
            return row
        result=self.evaluate([measured('resistor_in','R1','current',current),
                              measured('resistor_heat','R1','power',current**2*1000),
                              measured('capacitor_in','C1','current',current),
                              measured('capacitor_out','C1','current',-current,reverse=True)])
        self.assertEqual(result['verdict'],'pass',result['findings'])
        run=result['runs'][0]
        self.assertEqual(len(run['electrical_reference']['component_stress']),2)
        self.assertNotIn('Vprobe',(Path(run['artifact_directory'])/'circuit.cir').read_text())
        self.assertEqual({m.get('current_observation') for m in run['models']},{'native_branch_current'})
    def test_current_limited_non_sinking_source_releases_charged_board(self):
        self.scenario.update(engine='spice_transient',step_s=1e-6,stop_s=.004)
        self.scenario['drives'][0].update(pwl=[[0.,5.],[.001,5.],[.001000001,0.],[.004,0.]],non_sinking_supply=dict(current_limit_a=.001,output_resistance_ohm=1.))
        self.scenario['boundary_loads']=[dict(positive=terminal('J2'),negative=terminal('J2','2'),capacitance_f=1e-6),dict(positive=terminal('J3'),negative=terminal('J3','2'),resistance_ohm=2000.)]
        # Current-limited precharge: 1mA*2k=2V. After disconnect, only the
        # 2k load discharges C (tau2ms); the supply cannot short R1 to ground.
        expected=2*np.exp(-.001/.002)
        voltage=dict(id='stored_voltage',description='Independent no-sink RC discharge law.',positive=terminal('J2'),negative=terminal('J2','2'),quantity='voltage',minimum=expected*.999,maximum=expected*1.001,scenarios=['case'],time_window_s=[.002,.002])
        result=self.evaluate([voltage,requirement('limited_supply','J1','current',-.00100001,-.00099999,time_window_s=[.0002,.0005]),requirement('disconnected_supply','J1','current',-1e-12,1e-12,time_window_s=[.00101,.0039])])
        self.assertEqual(result['verdict'],'pass',result['findings'])
        self.assertEqual(result['runs'][0]['electrical_reference']['sources'][0]['non_sinking_supply']['current_limit_a'],.001)
    def add_screw_and_probe(self):
        screw=self.state['components']['J1'];screw['library_id']='Connector:Screw_Terminal_01x02'
        for pin in screw['pins']:pin['electrical_type']='passive'
        self.state['components']['TP1']=dict(id='TP1',ref='TP1',library_id='Connector:TestPoint',value='TestPoint',pins=[dict(pin_id='1',number='1',electrical_type='passive')])
        self.records['TP1']=copy.deepcopy(self.records['J1'])
        self.state['nets']['output']['pins'].append(dict(component_id='TP1',pin_id='1'))
    def test_screw_source_and_probe_observe_real_loaded_divider(self):
        self.add_screw_and_probe()
        # 1k in series with a 1k||2k load. The test loop must not add any load.
        req=dict(id='test_loop',description='Passive test loop at a loaded divider output',positive=terminal('TP1'),negative=terminal('J1','2'),quantity='voltage',minimum=2.-1e-9,maximum=2.+1e-9,scenarios=['case'])
        result=self.evaluate([req,requirement('screw_i','J1','current',-.003000001,-.002999999)])
        self.assertEqual(result['verdict'],'pass',result['findings'])
    def test_probe_is_not_a_fixture_drive_even_on_the_connector_net(self):
        self.add_screw_and_probe()
        self.scenario['drives'][0]['positive']=terminal('TP1')
        result=self.evaluate([requirement('load_i','J2','current',.00199,.00201)])
        self.assertEqual(result['verdict'],'fail')
        self.assertIn('PORT_MEASUREMENT_REQUIRES_EXPLICIT_CONNECTOR_FIXTURE_TERMINAL',{f['code'] for f in result['findings']})
    def test_port_observation_preserves_original_circuit_deck(self):
        for engine in ['spice_op','spice_transient','spice_ac']:
            raw=copy.deepcopy(self.scenario);raw.update(engine=engine)
            if engine=='spice_transient':raw.update(step_s=1e-7,stop_s=.001)
            if engine=='spice_ac':raw.update(frequency_hz=[100.,1000.],points=3)
            for load in ['resistor','capacitor','rl']:
                case=copy.deepcopy(raw)
                if load=='capacitor':case['boundary_loads'][0].pop('resistance_ohm');case['boundary_loads'][0]['capacitance_f']=1e-6
                if load=='rl':case['boundary_loads'][0]['inductance_h']=1.
                scenario=functional.Scenario.model_validate(case)
                original=functional.compile_spice(self.state,scenario,{},self.records)[0]
                observed=functional.compile_spice(self.state,scenario,{},self.records,instrument_ports=True)[0]
                # Saving a native capacitor current adds output only, no MNA nodes/elements.
                self.assertEqual(original,''.join(line for line in observed.splitlines(keepends=True) if not line.startswith('.save ')),(engine,load))
    def test_component_dissipation_cannot_be_replaced_by_port_transfer(self):
        req=[requirement('port','J2','current',0.,1.),dict(requirement('wrong','J3','power',0.,1.),measurement_scope='component')]
        result=self.evaluate(req)
        self.assertEqual(result['verdict'],'fail');self.assertIn('MEASUREMENT_COMPONENT_OR_PORT_SCOPE_MISMATCH',{f['code'] for f in result['findings']})
    def test_internal_resistor_cannot_be_claimed_as_external_port(self):
        result=self.evaluate([requirement('wrong','R1','current',0.,1.)])
        self.assertEqual(result['verdict'],'fail');self.assertIn('MEASUREMENT_COMPONENT_OR_PORT_SCOPE_MISMATCH',{f['code'] for f in result['findings']})


class PortContractTests(unittest.TestCase):
    def test_noise_requires_measured_matching_bias_and_source_bound_band(self):
        from pcb_mcp import noise_analysis
        drive=dict(positive=terminal('J1'),negative=terminal('J1','2'),dc_v=5.,ac_v=1.)
        scenario=dict(id='case',engine='spice_noise',drives=[drive],frequency_hz=[20.,20000.])
        row=dict(requirement('noise','J2','noise_voltage_rms',0.,1e-6),measurement_scope='component')
        with self.assertRaisesRegex(ValueError,'NOISE_MATCHING_DC_BIAS_REQUIRED'):
            functional.FunctionalContract.model_validate(dict(description='Noise must have the identical bias measured.',requirements=[row],scenarios=[scenario]))
        scenario=functional.Scenario.model_validate(scenario)
        evidence=[dict(reference='U1',model_id='a'*64)]
        with self.assertRaisesRegex(ValueError,'NOISE_MODEL_COVERAGE_MISSING: U1'):
            noise_analysis.evaluate('',None,scenario,'n2','0',evidence,{'U1':({},None)},'native')
        model=dict(spice_noise_scope=dict(frequency_hz=[100.,20000.]))
        with self.assertRaisesRegex(ValueError,'NOISE_MODEL_BAND_UNCOVERED: U1'):
            noise_analysis.evaluate('',None,scenario,'n2','0',evidence,{'U1':(model,None)},'native')

    def test_bandlimited_voltage_requires_a_resolved_window_and_fine_transient_grid(self):
        row=dict(requirement('ripple','J1','voltage',0.,.1,aggregation='peak_to_peak',time_window_s=[.001,.002]),measurement_scope='component')
        self.assertNotIn('lowpass',functional.FunctionalRequirement.model_validate(row).model_dump())
        row['lowpass']=dict(response='single_pole',cutoff_hz=1e4)
        with self.assertRaisesRegex(ValueError,'LOWPASS_REQUIRES_VOLTAGE_AGGREGATION_AND_NONZERO_WINDOW'):
            functional.FunctionalRequirement.model_validate(dict(row,time_window_s=None))
        with self.assertRaisesRegex(ValueError,'LOWPASS_TRANSIENT_STEP_TOO_COARSE'):
            functional.FunctionalContract.model_validate(dict(description='Coarse transient trace cannot score ripple.',requirements=[row],
                scenarios=[dict(id='case',engine='spice_transient',stop_s=.002,step_s=1e-5)]))
    def test_native_passive_measurement_is_explicit_and_transient_only(self):
        self.assertNotIn('passive_current_observation',functional.Scenario(id='case',engine='spice_op').model_dump())
        with self.assertRaisesRegex(ValueError,'NATIVE_PASSIVE_CURRENT_REQUIRES_TRANSIENT'):
            functional.Scenario(id='case',engine='spice_op',passive_current_observation='native')
    def test_source_only_fixture_cannot_silently_become_ac_or_negative_source(self):
        drive=dict(positive=terminal('J1'),negative=terminal('J1','2'),dc_v=5.,non_sinking_supply=dict(current_limit_a=.5,output_resistance_ohm=.01))
        with self.assertRaisesRegex(ValueError,'NEGATIVE_SETPOINT'):functional.Drive.model_validate(dict(drive,dc_v=-1.))
        with self.assertRaisesRegex(ValueError,'REQUIRES_SPICE_DC_OR_TRANSIENT'):functional.Scenario(id='case',engine='spice_ac',frequency_hz=[10.,100.],drives=[drive])
    def test_scalar_capacitor_current_cannot_broadcast_over_a_trace(self):
        probes={'J1':[dict(scope='external_port',vector='derived',terms=[dict(kind='direct',vector='cap',factor=1)])]}
        with self.assertRaisesRegex(ValueError,'SPICE_VECTOR_LENGTH_MISMATCH'):
            port_currents.materialize(probes,{'time':np.arange(3),'cap':np.array([1.])},'spice_transient')
    def test_voltage_cannot_claim_port_current_scope(self):
        raw=requirement('wrong','J1','voltage',0.,5.)
        with self.assertRaisesRegex(ValueError,'SCOPE_REQUIRES_CURRENT_OR_POWER'):functional.FunctionalRequirement.model_validate(raw)
