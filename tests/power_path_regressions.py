"""Independent analytic chains and actual KiCad direct contacts, not a green count."""
import copy,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/power-path-review';sys.path.insert(0,str(R/'src'))
from pcb_mcp.power_paths import selected_path
class Paths(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.nodes=json.loads((W/'native-contact-fixture.json').read_text())['nodes'];cls.src={u for u,x in cls.nodes.items() if x.get('reference')=='SRC'};cls.dst={u for u,x in cls.nodes.items() if x.get('reference')=='DST'}
 def test_two_real_tracks_are_not_a_zero_resistance_cluster(self):
  r=selected_path(self.nodes,self.src,self.dst,35.);self.assertAlmostEqual(r['track_reference_ohm'],1.72e-8*.020/(.0003*.000035),12);self.assertAlmostEqual(r['selected_track_length_mm'],20.,8)
 def test_missing_connection_fails(self):
  n=copy.deepcopy(self.nodes)
  for u in self.src:n[u]['contacts']=[]
  with self.assertRaises(ValueError):selected_path(n,self.src,self.dst,35.)
 def test_unknown_or_invalid_geometry_fails(self):
  for change in [dict(kind='unsupported'),dict(width_mm=0.),dict(length_mm=-1.)]:
   n=copy.deepcopy(self.nodes);next(x for x in n.values() if x['kind']=='track').update(change)
   with self.assertRaises(ValueError):selected_path(n,self.src,self.dst,35.)
 def test_cross_net_contact_fails(self):
  n=copy.deepcopy(self.nodes);next(x for x in n.values() if x['kind']=='track')['net']='OTHER'
  with self.assertRaises(ValueError):selected_path(n,self.src,self.dst,35.)
 def test_width_changes_resistance_inversely(self):
  n=copy.deepcopy(self.nodes)
  for x in n.values():
   if x['kind']=='track':x['width_mm']*=2
  a=selected_path(self.nodes,self.src,self.dst,35.);b=selected_path(n,self.src,self.dst,35.);self.assertAlmostEqual(a['track_reference_ohm'],2*b['track_reference_ohm'],12)
 def test_old_layout_reference_remains_failed(self):
  from pcb_mcp import power_paths,power_integrity
  s=json.loads((W/'original-diagnostic/spec.json').read_text());a=json.loads((W/'original-native-geometry.json').read_text());r=power_paths.evaluate(s,a,power_integrity.evaluate(s)['rails']);m=next(x for x in r if x['reference']=='U1');self.assertEqual(m['status'],'margin_not_established');self.assertLess(m['margin_reference_v'],0.)
if __name__=='__main__':unittest.main(verbosity=2)
