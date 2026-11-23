import unittest
from pcb_mcp.power_drive import resistive_drive


class PowerDriveTests(unittest.TestCase):
    def state(self):
        s=dict(nets={n:dict(kind='power' if n!='g' else 'ground',pins=[]) for n in ('a','b','c','g')},components={},part_bindings={},power_sources={'s':dict(net_id='a',ground_net_id='g')})
        for ref,left,right in [('R1','a','b'),('R2','b','c')]:
            s['components'][ref]=dict(id=ref,ref=ref,library_id='Device:R',value='1',pins=[dict(pin_id=n,electrical_type='passive') for n in ('1','2')])
            for net,pin in [(left,'1'),(right,'2')]:s['nets'][net]['pins'].append(dict(component_id=ref,pin_id=pin))
            s['part_bindings'][ref]=dict(kind='resistor',facts=dict(value_ohm=1))
        return s
    def test_source_fed_chain_gets_annotation(self):
        derived,paths=resistive_drive(self.state());self.assertEqual(derived,{'b','c'});self.assertEqual(len(paths),2)
    def test_capacitor_unbound_or_changed_value_cannot_feed_rail(self):
        for change in ('capacitor','unbound','value'):
            with self.subTest(change=change):
                s=self.state()
                if change=='capacitor':s['components']['R1']['library_id']='Device:C'
                elif change=='unbound':del s['part_bindings']['R1']
                else:s['part_bindings']['R1']['facts']['value_ohm']=10
                self.assertEqual(resistive_drive(s)[0],set())
    def test_ground_and_signal_cannot_seed_positive_supply(self):
        s=self.state();s['power_sources']['s']['net_id']='g';self.assertEqual(resistive_drive(s)[0],set())
        s=self.state();s['nets']['b']['kind']='signal';self.assertEqual(resistive_drive(s)[0],set())
    def test_actual_power_out_can_feed_resistor(self):
        s=self.state();s['power_sources']={}
        s['components']['U']=dict(id='U',ref='U',library_id='Other:X',value='X',pins=[dict(pin_id='p',electrical_type='power_out')])
        s['nets']['a']['pins'].append(dict(component_id='U',pin_id='p'))
        self.assertEqual(resistive_drive(s)[0],{'b','c'})
