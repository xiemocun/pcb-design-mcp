"""Synthetic reference tests, not qualification of the intercom hardware."""
import copy,unittest
from unittest.mock import patch
from pcb_mcp import dc_power_tree as dc

def fixture():
    s=dict(id='p',revision=1,components={},nets={},part_bindings={},footprint_bindings={})
    specs=[('J','connector',[('1','IN'),('2','GND')],{}),('U','regulator',[('1','IN'),('2','OUT'),('3','GND')],{}),('L','mcu',[('1','OUT'),('2','GND')],dict(supply_min_v=3.,supply_max_v=3.6)),('R','resistor',[('1','OUT'),('2','GND')],dict(value_ohm=1000.,tolerance_fraction=.01,temperature_coefficient_ppm_per_k=100.,rated_power_w=.1))]
    for name,kind,pins,facts in specs:
        c=dict(id=name,ref=name,library_id='fixture:'+name,value='fixture',pins=[]);s['components'][name]=c
        for num,net in pins:
            c['pins'].append(dict(pin_id=name+num,physical_numbers=[num]));s['nets'].setdefault(net,dict(id=net,name=net,kind='ground' if net=='GND' else 'power',pins=[]))['pins'].append(dict(component_id=name,pin_id=name+num))
        s['part_bindings'][name]=dict(candidate_id=name,definition_hash=name*64,evidence_status='available',kind=kind,facts=facts)
        s['footprint_bindings'][name]=dict(definition_hash='x'*64)
    load=s['components']['L'];expected={('L','L1'):(load,load['pins'][0])}
    m=dict(circuit_sha256=dc.circuit_hash(s),rails=[dict(name=n,minimum_v=v-.1,nominal_v=v,maximum_v=v+.1,basis='Synthetic test source interval') for n,v in [('IN',5.),('OUT',3.3)]],loads=[dict(reference='L',pin='1',current_a=.1,reason='Synthetic load test budget')],stage_references=['U'],reserves_a={'IN':0.,'OUT':0.},scenarios=[dict(name='on',condition='Synthetic enabled test state',sources=[dict(net='IN',reference='J',pin='1',capacity_a=1.,reason='Synthetic source boundary')],stages=[dict(reference='U',kind='buck',input_net='IN',output_net='OUT',input_pins=['1'],output_pins=['2'],input_range_v=[4.,6.],max_output_a=.5,efficiency=.8,reason='Synthetic converter efficiency')])],resistor_temperature_span_c=50.,conditions='Synthetic assumptions solely for arithmetic and rejection tests.')
    return s,m,expected

class DCTreeTests(unittest.TestCase):
    def evaluate(self,s,m,expected):
        with patch('pcb_mcp.parts.catalog',return_value=list(s['part_bindings'].values())),patch('pcb_mcp.parts.binding_valid',return_value=True),patch('pcb_mcp.power_budget.expected',return_value=(expected,{})):
            return dc.evaluate(s,m,'synthetic')
    def codes(self,r):return {x['code'] for x in r['findings'] if x['severity']=='error'}
    def test_current_and_loss_use_consistent_output_power(self):
        s,m,e=fixture();r=self.evaluate(s,m,e);self.assertFalse(self.codes(r));stage=r['scenarios'][0]['stages'][0]
        self.assertAlmostEqual(stage['output_demand_a'],.1+5.1/985.)
        self.assertAlmostEqual(stage['input_demand_a'],stage['output_demand_a']*3.4/(4.9*.8))
        self.assertAlmostEqual(stage['conversion_loss_reference_w'],stage['output_demand_a']*3.4*.25)
    def test_changed_wire_invalidates_model(self):
        s,m,e=fixture();s['components']['L']['value']='changed';self.assertIn('DC_TREE_CIRCUIT_CHANGED',self.codes(self.evaluate(s,m,e)))
    def test_missing_load_does_not_pass(self):
        s,m,e=fixture();m['loads'][0]['pin']='2';self.assertIn('DC_TREE_LOAD_COVERAGE',self.codes(self.evaluate(s,m,e)))
    def test_supply_overvoltage_is_error(self):
        s,m,e=fixture();m['rails'][1]['maximum_v']=4.;self.assertIn('DC_TREE_LOAD_VOLTAGE',self.codes(self.evaluate(s,m,e)))
    def test_converter_current_limit_is_error(self):
        s,m,e=fixture();m['loads'][0]['current_a']=.7;self.assertIn('DC_TREE_STAGE_OUTPUT_CAPACITY',self.codes(self.evaluate(s,m,e)))
    def test_source_capacity_is_error(self):
        s,m,e=fixture();m['scenarios'][0]['sources'][0]['capacity_a']=.01;self.assertIn('DC_TREE_SOURCE_CAPACITY',self.codes(self.evaluate(s,m,e)))
    def test_mismatched_power_pin_is_error(self):
        s,m,e=fixture();m['scenarios'][0]['stages'][0]['input_pins']=['2'];self.assertIn('DC_TREE_STAGE_PIN',self.codes(self.evaluate(s,m,e)))
    def test_path_needs_explicit_drop_budget(self):
        s,m,e=fixture();st=m['scenarios'][0]['stages'][0];st['kind']='power_path';st['efficiency']=None
        with self.assertRaises(ValueError):dc.DCPowerTree.model_validate(m)
        st['path_drop_budget_v']=.4;r=self.evaluate(s,m,e);self.assertAlmostEqual(r['scenarios'][0]['stages'][0]['conversion_loss_reference_w'],r['scenarios'][0]['stages'][0]['output_demand_a']*.4)

if __name__=='__main__':unittest.main()
