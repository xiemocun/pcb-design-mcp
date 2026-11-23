"""Static source bounds and uncovered conditions, with independent power equations."""
import copy,unittest
from pathlib import Path
from pcb_mcp import digital_power,digital_truth,functional


def fixture(bit):
    state=dict(components={},nets={})
    for ref,numbers in [('J1',['1','2']),('J2',['1','2']),('J3',['1','2']),('U1',['2','4','5','3'])]:
        state['components'][ref]=dict(id=ref,ref=ref,library_id='Connector_Generic:Conn_01x02' if ref[0]=='J' else 'Synthetic:Gate',pins=[dict(number=n,pin_id=n) for n in numbers])
    for nid,pairs in [('P',[('J1','1'),('U1','5')]),('G',[('J1','2'),('J2','2'),('J3','2'),('U1','3')]),('A',[('J2','1'),('U1','2')]),('Y',[('U1','4'),('J3','1')])]:state['nets'][nid]=dict(id=nid,pins=[dict(component_id=r,pin_id=n) for r,n in pairs])
    model=dict(engine='digital_truth',pins=['2','4','5','3'],supply_range_v=[5.5,5.5],temperature_range_c=[25.,25.],conditions='Synthetic static model, not manufacturer evidence',truth=dict(inputs=['2'],outputs=['4'],supply=['5','3'],rows={'0':'0','1':'1'},vil_max_v=1.88,vih_min_v=3.33,vol_max_v=.1,voh_min_v=5.4,input_leakage_max_a=5e-6,output_current_max_a=100e-6),static_supply_current=dict(maximum_a=10e-6,input_test_levels_v=[0.,5.5],output_condition='unloaded',lower_bound_method='nonnegative_static_dissipation',conditions='Synthetic exact input test condition'))
    records={ref:dict(kind='connector' if ref[0]=='J' else 'logic_gate',definition_hash='b'*64,facts={}) for ref in state['components']}
    terminal=lambda r,n='1':dict(ref=r,number=n)
    scenario=functional.Scenario.model_validate(dict(id='static',engine='digital_truth',drives=[dict(positive=terminal('J1'),negative=terminal('J1','2'),dc_v=5.5),dict(positive=terminal('J2'),negative=terminal('J2','2'),dc_v=5.5*bit)]))
    return state,scenario,{'U1':(model,Path('a'*64))},records


def evaluate(state,scenario,models,records):
    mapping=functional.terminals(state);logic=digital_truth.evaluate(state,scenario,models,records,mapping,[])
    return digital_power.reference(state,scenario,models,records,mapping,logic)


class StaticCurrentTests(unittest.TestCase):
    def test_ideal_unloaded_wire_does_not_require_a_particular_gate_architecture(self):
        state,scenario,models,records=fixture(1);state['components'].pop('U1');records.pop('U1');models.clear()
        for net in state['nets'].values():net['pins']=[p for p in net['pins'] if p['component_id']!='U1']
        state['nets']['A']['pins']+=state['nets'].pop('Y')['pins']
        r=evaluate(state,scenario,models,records);self.assertTrue(r['static_supply_reference_complete']);self.assertTrue(all(s['min_current_a']==s['max_current_a']==0 for s in r['sources']))
    def test_static_voltage_measurement_is_the_output_interval_not_the_boolean(self):
        state,scenario,models,records=fixture(1);raw=dict(id='out_v',description='Synthetic explicit high-output voltage bound',positive=dict(ref='J3',number='1'),negative=dict(ref='J3',number='2'),quantity='voltage',minimum=5.4,maximum=5.5,scenarios=['static'])
        req=functional.FunctionalRequirement.model_validate(raw);contract=functional.FunctionalContract.model_validate(dict(description='Synthetic digital voltage check',requirements=[raw],scenarios=[scenario.model_dump(mode='json')]))
        r=digital_truth.evaluate(state,scenario,models,records,functional.terminals(state),[req])['measurements'][0]
        self.assertTrue(r['passed']);self.assertEqual((r['minimum'],r['maximum']),(5.4,5.5))
    def test_logic_one_does_not_waive_a_tighter_unproven_high_voltage(self):
        state,scenario,models,records=fixture(1);req=functional.FunctionalRequirement.model_validate(dict(id='out_v',description='Synthetic unproven narrower high-voltage interval',positive=dict(ref='J3',number='1'),negative=dict(ref='J3',number='2'),quantity='voltage',minimum=5.4,maximum=5.45,scenarios=['static']))
        self.assertFalse(digital_truth.evaluate(state,scenario,models,records,functional.terminals(state),[req])['measurements'][0]['passed'])
    def test_low_input_has_no_external_input_energy_for_backfeed(self):
        r=evaluate(*fixture(0));self.assertTrue(r['static_supply_reference_complete'])
        self.assertEqual(r['sources'][0]['min_current_a'],0.);self.assertAlmostEqual(r['sources'][0]['max_current_a'],10e-6)
        self.assertAlmostEqual(r['component_stress'][0]['maximum_dissipation_w'],5.5*10e-6)
    def test_high_input_reverse_current_is_bounded_not_assumed_zero(self):
        r=evaluate(*fixture(1));self.assertAlmostEqual(r['sources'][0]['min_current_a'],-5e-6);self.assertAlmostEqual(r['sources'][0]['max_current_a'],10e-6)
        self.assertAlmostEqual(r['component_stress'][0]['maximum_dissipation_w'],5.5*(10e-6+5e-6))
        self.assertFalse(r['stress_reference_complete'])
    def test_input_on_same_supply_adds_input_leakage_and_cannot_generate_board_power(self):
        state,scenario,models,records=fixture(1)
        state['nets']['P']['pins']+=state['nets'].pop('A')['pins'];scenario=scenario.model_copy(update=dict(drives=scenario.drives[:1]))
        r=evaluate(state,scenario,models,records);self.assertAlmostEqual(r['sources'][0]['max_current_a'],15e-6);self.assertEqual(r['sources'][0]['min_current_a'],0.)
    def test_valid_logic_at_an_unpublished_current_test_input_stays_uncovered(self):
        state,scenario,models,records=fixture(1);raw=scenario.model_dump(mode='json');raw['drives'][1]['dc_v']=3.4
        r=evaluate(state,functional.Scenario.model_validate(raw),models,records);self.assertFalse(r['static_supply_reference_complete']);self.assertIn('DIGITAL_QUIESCENT_INPUT_CONDITION_UNCOVERED',{f['code'] for f in r['findings']})
    def test_legacy_truth_model_does_not_acquire_an_invented_current(self):
        state,scenario,models,records=fixture(1);models['U1'][0].pop('static_supply_current')
        r=evaluate(state,scenario,models,records);self.assertFalse(r['static_supply_reference_complete']);self.assertEqual(r['sources'],[])
    def test_cascaded_input_leakage_is_not_unloaded_output(self):
        state,scenario,models,records=fixture(1);state['components']['U2']=copy.deepcopy(state['components']['U1']);state['components']['U2'].update(id='U2',ref='U2')
        for nid,pin in [('P','5'),('G','3'),('Y','2')]:state['nets'][nid]['pins'].append(dict(component_id='U2',pin_id=pin))
        state['nets']['Y2']=dict(id='Y2',pins=[dict(component_id='U2',pin_id='4')]);models['U2']=copy.deepcopy(models['U1']);records['U2']=records['U1']
        r=evaluate(state,scenario,models,records);self.assertFalse(r['static_supply_reference_complete']);self.assertIn('DIGITAL_QUIESCENT_UNLOADED_OUTPUT_REQUIRED',{f['code'] for f in r['findings']})
    def test_an_output_powering_another_chip_is_not_unloaded(self):
        state,scenario,models,records=fixture(1);models['U1'][0]['truth']['voh_min_v']=5.5
        state['components']['U2']=copy.deepcopy(state['components']['U1']);state['components']['U2'].update(id='U2',ref='U2')
        for nid,pin in [('Y','5'),('G','3'),('A','2')]:state['nets'][nid]['pins'].append(dict(component_id='U2',pin_id=pin))
        state['nets']['Y2']=dict(id='Y2',pins=[dict(component_id='U2',pin_id='4')]);models['U2']=copy.deepcopy(models['U1']);records['U2']=records['U1']
        r=evaluate(state,scenario,models,records);self.assertFalse(r['static_supply_reference_complete']);self.assertIn('DIGITAL_QUIESCENT_UNLOADED_OUTPUT_REQUIRED',{f['code'] for f in r['findings']})


if __name__=='__main__':unittest.main()
