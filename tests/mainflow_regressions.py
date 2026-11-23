"""Failure-gate regressions; mocks here are not end-to-end backend evidence."""
import copy,subprocess,sys,unittest
from pathlib import Path
from unittest.mock import patch
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import prototype_policy,pcb_service,reset_timing
from pcb_mcp.service import Service
from pcb_mcp.placement_rules import reference_plan
from pcb_mcp.models import ReviewPrepare
from pcb_mcp.common import DomainError
class Regression(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.service=Service(R/'data');cls.state=cls.service.load('7b1ac6b23fd14e1ba887b4bc2cd25a0a')
 def test_reset_unreachable(self):
  for v in [3.3,4.0]:self.assertIsNone(reset_timing.crossing_ms(v,10,1,3.3,0))
  self.assertEqual(reset_timing.crossing_ms(.6,10,1,3.3,1),0)
  for threshold in [.6,2.475]:
   t=reset_timing.crossing_ms(threshold,10,1,3.3,0);self.assertAlmostEqual(reset_timing.en_voltage(t,10,1,3.3,0),threshold,places=12)
  with self.assertRaises(ValueError):reset_timing.crossing_ms(.6,0,1,3.3,0)
 def test_java_stub_and_timeout(self):
  for result in [subprocess.CompletedProcess([],1,'','Unable to locate Java Runtime'),subprocess.CompletedProcess([],0,'','launcher stub'),subprocess.CompletedProcess([],0,'','openjdk version "17.0.1"'),subprocess.CompletedProcess([],0,'','openjdk version "21.0.1"'),subprocess.CompletedProcess([],0,'','openjdk version "24.0.1"')]:
   with patch.object(pcb_service.subprocess,'run',return_value=result):self.assertFalse(pcb_service.java_runtime()['available'])
  with patch.object(pcb_service.subprocess,'run',side_effect=subprocess.TimeoutExpired('java',5)):self.assertFalse(pcb_service.java_runtime()['available'])
  with patch.object(pcb_service.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','openjdk version "25.0.1"')):self.assertTrue(pcb_service.java_runtime()['available'])
 def test_scoped_uncertainties_preserve_new_errors(self):
  f=[dict(code=x,severity='error',message=x) for x in ['BUCK_DYNAMIC_THERMAL_UNQUALIFIED','BUCK_INPUT_RANGE','NEW_UNKNOWN_ERROR']]
  self.assertEqual([x['severity'] for x in prototype_policy.findings_for_geometry(f)],['warning','error','error']);self.assertEqual(f[0]['severity'],'error')
 def test_legacy_txs_reference_remains_rejected(self):
  r=prototype_policy.assessment(self.service,self.state)
  self.assertEqual(r['verdict'],'fail');self.assertFalse(r['physical_qualified'])
  self.assertTrue(any(f['code']=='MODULE_LOGIC_MARGIN_UNPROVEN' and f['severity']=='error' for f in r['findings']))
 def test_known_voltage_error_blocks(self):
  s=copy.deepcopy(self.state);s['power_envelope']['max_v']=6.0;r=prototype_policy.assessment(self.service,s)
  self.assertEqual(r['verdict'],'fail');self.assertTrue(any(f['code']=='LDO_INPUT_RANGE' and f['severity']=='error' for f in r['findings']))
 def test_additions_allowed_missing_core_rejected(self):
  s=copy.deepcopy(self.state);c=copy.deepcopy(next(c for c in s['components'].values() if c['ref']=='C4'));c.update(id='f'*32,ref='C14');s['components'][c['id']]=c
  original=reference_plan(self.state);self.assertEqual(original,reference_plan(s))
  c['library_id']='Unknown:Extra';self.assertEqual(reference_plan(s),original)
  del s['components'][next(k for k,c in s['components'].items() if c['ref']=='U1')]
  with self.assertRaisesRegex(ValueError,'REQUIRED_COMPONENT_MISSING'):reference_plan(s)
 def test_production_purpose_forbidden(self):
  with self.assertRaises(ValueError):ReviewPrepare(project_id='a'*32,pcb_build_id='b'*32,purpose='production')
 def test_copper_rules_require_evidence_and_coverage(self):
  with patch.object(prototype_policy,'assessment',return_value=dict(verdict='eligible_for_prototype_review',findings=[],reports={})):
   with self.assertRaises(DomainError) as cm:self.service.export_assessment(self.state,dict(checks={}),Path('/tmp'))
   self.assertEqual(cm.exception.payload['code'],'COPPER_VALIDATION_REQUIRED')
   s=copy.deepcopy(self.state);s['pcb_copper_policy']['rules'].pop()
   with self.assertRaises(DomainError) as cm:self.service.export_assessment(s,dict(checks={}),Path('/tmp'))
   self.assertEqual(cm.exception.payload['code'],'COPPER_RULE_COVERAGE_REQUIRED')
 def test_absent_copper_policy_explicit_warning(self):
  s=copy.deepcopy(self.state);s['pcb_copper_policy']={}
  with patch.object(prototype_policy,'assessment',return_value=dict(verdict='eligible_for_prototype_review',findings=[],reports={})):
   r=self.service.export_assessment(s,dict(checks={}),Path('/tmp'))
  self.assertEqual(r['findings'][-1]['code'],'COPPER_POLICY_UNDECLARED')
if __name__=='__main__':unittest.main(verbosity=2)
