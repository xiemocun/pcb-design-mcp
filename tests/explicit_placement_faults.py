"""Reject malformed or stale placement contracts before native CAD execution."""
import unittest
from pcb_mcp.explicit_placement import validate,plan
from pcb_mcp.models import BoardConstraints

class PlacementFaults(unittest.TestCase):
    def setUp(self):
        self.s={'components':{'a':{},'b':{}},'nets':{'g':{'kind':'ground'},'v':{'kind':'power'}}}
        self.bc={'width_mm':80.,'height_mm':60.,'placement_profile':'explicit','placements':[{'component_id':c,'x_mm':x,'y_mm':20.,'rotation_deg':0} for c,x in [('a',20.),('b',40.)]],'ground_plane_net_id':'g','ground_plane_reason':'Continuous signal return plane'}
    def test_complete_contract(self):
        validate(self.s,self.bc);self.s['board_constraints']=self.bc;self.assertEqual(plan(self.s)['a'],(20.,20.,0))
    def test_duplicate(self):
        self.bc['placements'][1]['component_id']='a'
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_removed_component(self):
        del self.s['components']['b']
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_missing_component(self):
        self.s['components']['c']={}
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_supply_plane_rejected(self):
        self.bc['ground_plane_net_id']='v'
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_outside_origin(self):
        self.bc['placements'][0]['x_mm']=81.
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_inverted_keepout(self):
        self.bc['copper_keepouts']=[dict(x_min_mm=20.,x_max_mm=10.,y_min_mm=2.,y_max_mm=4.,no_tracks=True,no_vias=True,no_fills=True)]
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_old_profile_unchanged(self):
        validate(self.s,dict(width_mm=80.,height_mm=60.,placement_profile='bounded_grid'))
    def route(self):
        self.bc['local_routes']=[dict(net_id='v',layer='F.Cu',width_mm=.25,points=[dict(x_mm=20.,y_mm=20.),dict(x_mm=21.,y_mm=20.)])]
        return self.bc['local_routes'][0]
    def test_stale_route_net(self):
        self.route()['net_id']='absent'
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_route_copper_edge(self):
        self.route()['points'][0]['x_mm']=.5
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_zero_length_route(self):
        r=self.route();r['points'][1]=dict(r['points'][0])
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_valid_route(self):
        self.route();validate(self.s,self.bc)

class ViaFaults(PlacementFaults):
    def setUp(self):
        super().setUp();self.bc['min_drill_mm']=.2
        self.v=dict(net_id='g',x_mm=20.,y_mm=20.,diameter_mm=.5,drill_mm=.2,filled_capped=True,reason='Exposed-pad copper-capped fabrication requirement')
        self.bc['local_vias']=[self.v]
    def test_valid_via(self):validate(self.s,self.bc)
    def test_thin_annulus(self):
        self.v['drill_mm']=.4
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_via_drill_below_factory(self):
        self.bc['min_drill_mm']=.3
        with self.assertRaises(ValueError):validate(self.s,self.bc)
    def test_via_outside(self):
        self.v['x_mm']=.1
        with self.assertRaises(ValueError):validate(self.s,self.bc)

if __name__=='__main__':unittest.main()
