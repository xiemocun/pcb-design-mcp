"""Actual native mainboard plane and RF-exclusion fault injection."""
import copy,json,subprocess,sys,unittest,uuid
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import copper,design_checks
W=R.parents[1]/'work/mainboard-ground-return';K='/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9';RUN=uuid.uuid4().hex
class Mainboard(unittest.TestCase):
 def case(self,mode):
  run=RUN+'-'+self._testMethodName
  p=subprocess.run([K,str(W/'native_fault_case.py'),mode,run],capture_output=True,text=True,timeout=40)
  out=W/'fault-cases'/run/mode;(out/'native.log').write_text(p.stdout+p.stderr);self.assertEqual(p.returncode,0,p.stderr[-2000:])
  a=json.loads((out/'actual.json').read_text());s=json.loads((out/'spec.json').read_text());r=copper.evaluate(s,a,'native-fixture',True);(out/'copper.json').write_text(json.dumps(r,indent=2));self.actual=a;self.state=s;return a['ground_planes'],r
 def test_good(self):
  p,r=self.case('good');self.assertEqual(p['status'],'pass');self.assertEqual(r['geometry_verdict'],'pass');self.assertTrue(p['track_only_ground_connected']);self.assertTrue(p['native_refill_matches_saved']);self.assertFalse(p['full_return_inductance_qualified']);self.assertEqual(r['verdict'],'incomplete')
  self.assertEqual(design_checks.layout_report(self.state,self.actual,'fixture','routed')['verdict'],'pass')
  self.assertEqual(len(p['antenna_keepout_checks']),2);self.assertTrue(all(x['copper_overlap_polygons']==0 for x in p['antenna_keepout_checks']));self.assertTrue(all(z['direct_ground_pad_count']==z['expected_ground_pad_count'] for z in p['zones']))
 def test_layout_requires_complete_native_rf_proof(self):
  self.case('good')
  for mode in ['absent','one_layer','nonzero_overlap','not_refilled']:
   a=copy.deepcopy(self.actual);p=a['ground_planes']
   if mode=='absent':a.pop('ground_planes')
   elif mode=='one_layer':p['antenna_keepout_checks']=p['antenna_keepout_checks'][:1]
   elif mode=='nonzero_overlap':p['antenna_keepout_checks'][0]['copper_overlap_area_mm2']=.001
   else:p['native_refill_matches_saved']=False
   r=design_checks.layout_report(self.state,a,'fixture','routed')
   self.assertIn('RF_COPPER_ZONE_PROOF_UNSUPPORTED',{f['code'] for f in r['findings']},mode)
 def test_removed_keepout(self):
  p,r=self.case('removed_keepout');self.assertIn('GROUND_PLANE_RF_KEEPOUT_CHANGED',p['errors']);self.assertIn('GROUND_PLANE_RF_COPPER_INTRUSION',p['errors']);self.assertEqual(r['geometry_verdict'],'fail')
 def test_disabled_keepout(self):
  p,r=self.case('disabled_keepout');self.assertIn('GROUND_PLANE_RF_KEEPOUT_CHANGED',p['errors']);self.assertEqual(r['geometry_verdict'],'fail')
 def test_antenna_via(self):
  p,r=self.case('antenna_via');self.assertIn('GROUND_PLANE_RF_CONDUCTOR_INTRUSION',p['errors']);self.assertEqual(r['geometry_verdict'],'fail')
 def test_unstitched_polygon(self):
  p,r=self.case('unstiched_top_polygon');self.assertIn('GROUND_PLANE_TOP_ISLAND_UNSTITCHED',p['errors']);self.assertEqual(r['geometry_verdict'],'fail')
 def test_missing_local_vias(self):
  p,r=self.case('missing_local_vias');self.assertIn('GROUND_PLANE_LOCAL_VIAS_MISSING',p['errors']);self.assertEqual(r['geometry_verdict'],'fail')
 def test_missing_planes(self):
  p,r=self.case('missing_planes');self.assertIn('COPPER_GROUND_PLANE_MISSING',{f['code'] for f in r['findings']});self.assertEqual(r['geometry_verdict'],'fail')
 def test_stale_fill(self):
  p,r=self.case('stale_fill');self.assertIn('GROUND_PLANE_STALE_FILL',p['errors']);self.assertEqual(r['geometry_verdict'],'fail')
if __name__=='__main__':unittest.main(verbosity=2)
