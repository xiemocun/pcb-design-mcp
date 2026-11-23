import copy,unittest
from pcb_mcp.dc_limits import PassSwitchLimits
from pcb_mcp.dc_pass_network import propagate

def fixture():
    s=dict(components={},nets={},power_envelope=dict(ground_net_id='g',available_current_ma=500.,ambient_c=25.,ambient_range_c=[0.,60.]))
    for name in ('in','mid','out','g','en'):s['nets'][name]=dict(id=name,pins=[])
    models={}
    for cid,inp,out,r in [('a','mid','out',.4),('b','in','mid',.2)]:
        c=dict(id=cid,ref=cid,pins=[]);s['components'][cid]=c
        for number,n,typ in [('1',inp,'power_in'),('2',out,'power_out'),('3','g','power_in'),('4','en','input')]:
            c['pins'].append(dict(number=number,pin_id=number,electrical_type=typ));s['nets'][n]['pins'].append(dict(component_id=cid,pin_id=number))
        m=PassSwitchLimits(input_pin='1',output_pin='2',ground_pin='3',enable_pin='4',supply_range_v=[1.8,5.5],maximum_continuous_current_a=dict(points=[[-40.,1.5],[85.,1.5]]),maximum_on_resistance_ohm=r,conditions='Synthetic source model for interval and topology tests only.')
        models[cid]=(cid,dict(temperature_range_c=[-40.,85.]),m)
    return s,{'in':dict(min_v=4.75,max_v=5.25,nominal_v=5.),'g':dict(min_v=0.,max_v=0.,nominal_v=0.)},models

class SeriesPaths(unittest.TestCase):
    def test_reversed_iteration_propagates_full_capacity_bounds_without_claiming_enable(self):
        s,r,m=fixture();derived,rows=propagate(s,r,m)
        self.assertAlmostEqual(derived['out']['min_v'],4.45)
        self.assertEqual(derived['out']['max_v'],5.25)
        self.assertEqual(len(rows),2);self.assertTrue(all(x['enable_verified'] is False and x['off_leakage_verified'] is False for x in rows))
        self.assertIsNone(m['a'][2].maximum_off_leakage_a)
    def test_parallel_source_and_unqualified_ranges_do_not_acquire_an_envelope(self):
        s,r,m=fixture();r['mid']=dict(min_v=5.,max_v=5.,nominal_v=5.)
        with self.assertRaisesRegex(ValueError,'PARALLEL_DRIVER'):propagate(s,r,m)
        s,r,m=fixture();s['power_envelope']['ambient_range_c']=[0.,90.]
        with self.assertRaisesRegex(ValueError,'TEMPERATURE_OUTSIDE'):propagate(s,r,m)
        s,r,m=fixture();s['power_envelope']['available_current_ma']=2000.
        with self.assertRaisesRegex(ValueError,'CAPACITY_EXCEEDS'):propagate(s,r,m)
    def test_unmodeled_active_driver_is_not_ignored(self):
        s,r,m=fixture();s['components']['x']=dict(id='x',ref='x',pins=[dict(number='1',pin_id='1',electrical_type='output')]);s['nets']['mid']['pins'].append(dict(component_id='x',pin_id='1'))
        with self.assertRaisesRegex(ValueError,'ANOTHER_DRIVER'):propagate(s,r,m)

if __name__=='__main__':unittest.main()
