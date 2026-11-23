"""Regression for explicit no-connect handling on the real retained mainboard."""
import copy,json,sys,tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from pcb_mcp_client.transient import verified_circuit
ROOT=Path(__file__).resolve().parents[3];W=ROOT/'work/mainboard-transient'
sys.path.insert(0,str(W))
from adapt_models import adapt
class MainboardTransientTests(unittest.TestCase):
 def setUp(self):
  self.state=json.loads((ROOT/'outputs/pcb-design-mcp-v16/data/fb639744d29945359f393e331cfb34ab/state.json').read_text())
  self.xml=W/'mainboard-actual.xml'
 def check_bad(self,change):
  tree=ET.parse(self.xml);change(tree.getroot())
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'actual.xml';tree.write(p)
   with self.assertRaises(ValueError):verified_circuit(self.state,p)
 def test_actual_mainboard(self):
  components,pins=verified_circuit(self.state,self.xml)
  self.assertEqual(len(components),36);self.assertEqual(pins[('U7','1')],'VBUS_RAW')
  self.assertEqual(pins[('U3','1')],pins[('U3','2')])
 def test_removed_intent_rejected(self):
  self.state['pin_intents'].pop(next(iter(self.state['pin_intents'])))
  with self.assertRaisesRegex(ValueError,'MISMATCH'):verified_circuit(self.state,self.xml)
 def test_accidental_no_connect_short_rejected(self):
  def change(root):
   net=next(n for n in root.findall('nets/net') if n.get('name').startswith('unconnected-'))
   ET.SubElement(net,'node',{'ref':'U7','pin':'1'})
  self.check_bad(change)
 def test_unrecognized_isolated_pin_rejected(self):
  def change(root):
   net=next(n for n in root.findall('nets/net') if n.get('name').startswith('unconnected-'))
   net.find('node').set('pin','666')
  self.check_bad(change)
 def test_real_power_pin_swap_rejected(self):
  def change(root):
   for n in root.findall('nets/net/node'):
    if n.get('ref')=='U7' and n.get('pin') in ('1','6'):n.set('pin','6' if n.get('pin')=='1' else '1')
  self.check_bad(change)
 def test_no_connect_declared_but_connected_rejected(self):
  c=next(c for c in self.state['components'].values() if c['ref']=='U7')
  p=next(p for p in c['pins'] if p['number']=='1')
  self.state['pin_intents']['injected']={'intent':'no_connect','pin':{'component_id':c['id'],'pin_id':p['pin_id']}}
  with self.assertRaisesRegex(ValueError,'NO_CONNECT_PIN_CONNECTED'):verified_circuit(self.state,self.xml)
 def test_xor_adaptation_preserves_other_model_text(self):
  original='* comment\nE1 Y 0 VALUE {IF(V(A) > {VTHRESH} ^\n+ V(B) > {VTHRESH},1,0)}\nR1 A 0 10k\n'
  result,changes=adapt(original,1)
  self.assertEqual(result,original.replace(changes[0]['before'],changes[0]['after']))
  self.assertIn('!=',result)
 def test_unknown_xor_rejected(self):
  with self.assertRaisesRegex(ValueError,'UNSUPPORTED'):adapt('E1 Y 0 VALUE {A^B}',1)

from run_chain_external import analyze
import numpy as np
class ChainAnalyzerTests(unittest.TestCase):
 def wave(self):
  t=np.linspace(0,.035,35001);v=np.minimum(t/.001,1)*3.318
  en=3.318*(1-np.exp(-np.maximum(t-.001,0)/.01))
  return {'time':t,'v(v3v3)':v,'v(mcu_en)':en,'i(vraw)':np.full(t.shape,-.7)}
 def test_normal_synthetic_fixture(self):
  self.assertEqual(analyze(self.wave())['verdict'],'scenario_checks_pass')
 def test_supply_droop_fails(self):
  w=self.wave();w['v(v3v3)'][(w['time']>.025)&(w['time']<.026)]=2.5
  self.assertEqual(analyze(w)['verdict'],'scenario_checks_fail')
 def test_early_reset_release_fails(self):
  w=self.wave();w['v(mcu_en)']=w['v(v3v3)'].copy()
  self.assertEqual(analyze(w)['verdict'],'scenario_checks_fail')
 def test_truncated_window_rejected(self):
  w={k:v[:30000] for k,v in self.wave().items()}
  with self.assertRaisesRegex(ValueError,'INCOMPLETE'):analyze(w)

if __name__=='__main__':unittest.main()
