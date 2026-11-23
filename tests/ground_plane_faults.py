"""Native copper geometry faults plus service-level interpretation."""
import json,subprocess,sys,unittest,uuid
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import copper
W=R.parents[1]/'work/pulse-ground-plane';K='/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9'
RUN_ID=uuid.uuid4().hex
class Planes(unittest.TestCase):
 def run_case(self,mode):
  result=subprocess.run([K,str(W/'native_fault_case.py'),mode,RUN_ID],capture_output=True,text=True,timeout=40)
  (W/(mode+'-test.log')).write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,result.stderr[-1500:])
  folder=W/'fault-cases'/RUN_ID/mode;actual=json.loads((folder/'actual.json').read_text());state=json.loads((folder/'spec.json').read_text())
  report=copper.evaluate(state,actual,'native-fixture',True);(folder/'copper.json').write_text(json.dumps(report,indent=2));return actual['ground_planes'],report
 def test_good(self):
  p,r=self.run_case('good');self.assertEqual(p['status'],'pass');self.assertTrue(p['track_only_ground_connected']);self.assertTrue(p['native_refill_matches_saved']);self.assertFalse(p['zone_resistance_modeled']);self.assertEqual(r['geometry_verdict'],'pass');self.assertEqual(r['verdict'],'incomplete');self.assertNotEqual(r['reference_screen'],'pass')
 def test_empty_fill(self):
  p,r=self.run_case('empty_fill');self.assertIn('GROUND_PLANE_EMPTY_OR_SPLIT',p['errors']);self.assertEqual(r['verdict'],'fail')
 def test_wrong_net(self):
  p,r=self.run_case('wrong_net');self.assertIn('GROUND_PLANE_IDENTITY',p['errors']);self.assertEqual(r['verdict'],'fail')
 def test_missing_planes(self):
  p,r=self.run_case('missing_planes');self.assertIn('COPPER_GROUND_PLANE_MISSING',{x['code'] for x in r['findings']})
 def test_no_local_vias(self):
  p,r=self.run_case('no_local_vias');self.assertIn('GROUND_PLANE_LOCAL_VIAS_MISSING',p['errors']);self.assertEqual(r['verdict'],'fail')
 def test_broken_backbone(self):
  p,r=self.run_case('broken_backbone');self.assertIn('GROUND_PLANE_BACKBONE_BROKEN',p['errors']);self.assertEqual(r['verdict'],'fail')
 def test_stale_fill(self):
  p,r=self.run_case('stale_fill');self.assertIn('GROUND_PLANE_STALE_FILL',p['errors']);self.assertEqual(r['verdict'],'fail')
 def test_unknown_zone_stays_error(self):
  s=json.loads((W/'stitch-probe/spec.json').read_text());a=json.loads((W/'stitch-inspection-04.json').read_text());a.pop('ground_planes');r=copper.evaluate(s,a,'no-proof',True);self.assertIn('COPPER_ZONES_UNSUPPORTED',{x['code'] for x in r['findings']})
if __name__=='__main__':unittest.main(verbosity=2)
