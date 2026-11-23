"""Synthetic UART stream tests: never claim physical board results."""
import copy,importlib.util,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bench',R/'firmware/am2302/bench.py');bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)
EXPECTED=dict(project_id='test-project',revision=42,pcb_build_id='test-board',app_elf_sha256='a'*64,drive_gpio=26,read_gpio=27)
def fixture():
 out=[dict(type='session',host_elapsed_s=0.,origin='synthetic'),dict(type='phase',host_elapsed_s=0.,name='connected'),dict(type='serial',host_elapsed_s=.1,text=json.dumps(dict(event='boot',protocol=1,reset_reason=1,**EXPECTED)))]
 for i in range(1,290):
  t=i*2.5
  if t==605.:out.append(dict(type='phase',host_elapsed_s=t-.1,name='fault'))
  if t==667.5:out.append(dict(type='phase',host_elapsed_s=t-.1,name='recovered'))
  m=dict(sequence=i,uptime_ms=int(t*1000),status='error' if 605.<=t<667.5 else 'ok')
  if m['status']=='ok':m.update(temperature_c=25.3,humidity_rh=65.4)
  else:m['reason']='no_response_preamble'
  out.append(dict(type='serial',host_elapsed_s=t,text=json.dumps(m)))
 # Keep recovery longer than 60s.
 for i in range(290,295):
  t=i*2.5;out.append(dict(type='serial',host_elapsed_s=t,text=json.dumps(dict(sequence=i,uptime_ms=int(t*1000),status='ok',temperature_c=25.3,humidity_rh=65.4))))
 out.append(dict(type='end',host_elapsed_s=737.));return out
class Bench(unittest.TestCase):
 def setUp(self):self.r=fixture()
 def ev(self):return bench.analyze(self.r,EXPECTED)
 def change(self,index,**changes):
  m=json.loads(self.r[index]['text']);m.update(changes);self.r[index]['text']=json.dumps(m)
 def bad(self,code):r=self.ev();self.assertEqual(r['verdict'],'fail');self.assertIn(code,r['errors'])
 def test_synthetic_valid_stays_synthetic(self):
  r=self.ev();self.assertEqual(r['verdict'],'records_match_protocol');self.assertEqual(r['evidence_origin'],'synthetic');self.assertFalse(r['physical_function_qualified'])
 def test_missing_all_incomplete(self):self.assertNotEqual(bench.analyze([],EXPECTED)['verdict'],'records_match_protocol')
 def test_nonobject_record_rejected(self):self.r[0]=None;self.bad('INVALID_RECORD')
 def test_reboot(self):self.r.insert(10,dict(self.r[2],host_elapsed_s=self.r[9]['host_elapsed_s']));self.bad('UNEXPECTED_REBOOT')
 def test_brownout(self):self.change(2,reset_reason=9);self.bad('UNEXPECTED_INITIAL_RESET_REASON')
 def test_wrong_firmware(self):self.change(2,app_elf_sha256='b'*64);self.bad('BOOT_IDENTITY_MISMATCH_APP_ELF_SHA256')
 def test_missing_boot(self):self.r.pop(2);self.assertEqual(self.ev()['verdict'],'incomplete')
 def test_sequence_gap(self):self.r.pop(20);self.bad('SEQUENCE_GAP_OR_RESET')
 def test_implicit_uptime_reset(self):self.change(20,uptime_ms=1);self.bad('SAMPLE_INTERVAL_OR_UPTIME_RESET')
 def test_nan_reading(self):self.change(20,temperature_c=float('nan'));self.bad('INVALID_MEASUREMENT')
 def test_error_in_connected(self):self.change(20,status='error',reason='checksum');self.bad('PHASE_STATUS_MISMATCH_CONNECTED')
 def test_stale_value_in_fault(self):
  i=next(i for i,r in enumerate(self.r) if r['type']=='serial' and r['host_elapsed_s']>620);self.change(i,temperature_c=25.3);self.bad('STALE_VALUES_ON_ERROR')
 def test_no_recovery(self):
  i=next(i for i,r in enumerate(self.r) if r['type']=='serial' and r['host_elapsed_s']>680);self.change(i,status='error',reason='no_response_preamble');self.bad('PHASE_STATUS_MISMATCH_RECOVERED')
 def test_truncated_session(self):self.r.pop();self.assertEqual(self.ev()['verdict'],'incomplete')
 def test_malformed_json_not_ignored(self):self.r[20]['text']='{"sequence":';self.bad('MALFORMED_JSON_FRAME')
 def test_short_phase_not_pass(self):
  self.r=[r for r in self.r if r['host_elapsed_s']<700];self.r.append(dict(type='end',host_elapsed_s=701.));self.assertEqual(self.ev()['verdict'],'incomplete')
 def test_capture_trailing_silence(self):self.r[-1]['host_elapsed_s']=800.;self.bad('PHASE_SILENCE_RECOVERED')
 def test_fast_samples(self):self.change(20,uptime_ms=42501);self.bad('SAMPLE_INTERVAL_OR_UPTIME_RESET')
 def test_bad_time(self):self.r[20]['host_elapsed_s']=float('inf');self.bad('INVALID_HOST_TIME')
if __name__=='__main__':unittest.main(verbosity=2)
