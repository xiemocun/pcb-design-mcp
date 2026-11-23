"""Length and via limits must hold simultaneously on physical contact graphs."""
import unittest
from pcb_mcp.path_constraints import shortest_path, evaluate


class PathConstraints(unittest.TestCase):
    def graph(self):
        return {
            's':dict(kind='pad',net='FB',contacts=['a','long']),
            'a':dict(kind='track',net='FB',length_mm=1.,contacts=['s','v']),
            'v':dict(kind='via',net='FB',contacts=['a','b']),
            'b':dict(kind='track',net='FB',length_mm=1.,contacts=['v','t']),
            'long':dict(kind='track',net='FB',length_mm=10.,contacts=['s','t']),
            't':dict(kind='pad',net='FB',contacts=['b','long'])}

    def test_shorter_route_cannot_spend_an_unauthorized_via(self):
        nodes=self.graph()
        self.assertEqual(shortest_path(nodes,{'s'},{'t'},0)['length_upper_bound_mm'],10.)
        result=shortest_path(nodes,{'s'},{'t'},1)
        self.assertEqual((result['length_upper_bound_mm'],result['via_count']),(2.,1))

    def test_disconnected_and_cross_net_contacts_fail(self):
        nodes=self.graph();nodes['t']['net']='OTHER'
        with self.assertRaisesRegex(ValueError,'NET_MISMATCH'):shortest_path(nodes,{'s'},{'t'},0)
        with self.assertRaisesRegex(ValueError,'PADS_MISSING'):shortest_path(nodes,set(),{'t'},0)

    def test_invalid_native_length_cannot_be_used(self):
        nodes=self.graph();nodes['long']['length_mm']=float('nan')
        with self.assertRaisesRegex(ValueError,'LENGTH_INVALID'):shortest_path(nodes,{'s'},{'t'},0)

    def test_unrouted_status_is_explicitly_deferred(self):
        state={'components':{},'board_constraints':{'path_constraints':[dict(source=dict(component_id='source',pin_id='one'),target=dict(component_id='target',pin_id='two'),max_length_mm=3.,max_vias=0)]}}
        result=evaluate(state,{},'unrouted')
        self.assertEqual(result['measurements'][0]['status'],'deferred_until_routed')
        self.assertEqual(result['findings'],[])
        result=evaluate(state,{},'routed')
        self.assertEqual(result['findings'][0]['code'],'DECLARED_COPPER_PATH_FAILED')


if __name__=='__main__':unittest.main()
