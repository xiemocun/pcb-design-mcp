"""Real saved circuits, source-bound role inference and non-waivable failures."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R/'src'))
from pcb_mcp import usb_power, usb_psd, parts, prototype_policy
from pcb_mcp.service import Service


class PSD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analog = json.loads((R/'data/8f5b7aa0dac846ac961ebcfee740b6cc/state.json').read_text())
        cls.timer = json.loads((R/'data/a3074bbb123e45faa365985ab499686c/state.json').read_text())
    def setUp(self):
        self.s = copy.deepcopy(self.analog)
    def c(self, ref):
        return next(c for c in self.s['components'].values() if c['ref'] == ref)
    def report(self):
        r = usb_power.evaluate(self.s)
        return r, r['ports'][0]['default_power'], {f['code'] for f in r['findings'] if f['severity']=='error'}
    def move(self, ref, num, name):
        c = self.c(ref); p = dict(component_id=c['id'], pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
        for n in self.s['nets'].values(): n['pins'] = [q for q in n['pins'] if q != p]
        if name: next(n for n in self.s['nets'].values() if n['name']==name)['pins'].append(p)
    def test_power_only_classification_not_physical_qualification(self):
        r,p,c = self.report()
        self.assertEqual(p['role'], 'power_only_sink_candidate')
        self.assertEqual(p['default_current_permission_a'], .5)
        self.assertAlmostEqual(p['declared_dc_reference_a'], .004)
        self.assertIsNone(p['actual_current_upper_bound_a']); self.assertFalse(p['full_usb_qualified'])
        self.assertFalse({'USB_CC_CURRENT_NOT_DETECTED','USB_DEFAULT_MODE_UNQUALIFIED','USB_ATTACH_LOAD_POLICY_MISSING'} & c)
        self.assertTrue({'USB_PSD_EFFECTIVE_CAPACITANCE_UNPROVEN','USB_PSD_ATTACH_LOAD_UNPROVEN','USB_PSD_CURRENT_BOUND_UNPROVEN'} <= c)
        self.assertEqual(r['verdict'],'fail')
    def test_unpowered_input_path_is_retained(self):
        r,_,c = self.report(); self.assertIn('USB_EXTERNAL_INPUT_BACKFEED_PATH',c)
        rows=[x for x in r['findings'] if x['code']=='USB_EXTERNAL_INPUT_BACKFEED_PATH']
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['path'],['J2','U1.1','input_clamp','VBUS'])
        self.assertIsNone(rows[0]['actual_injection_current_upper_bound_a'])
    def test_budget_declaration_does_not_establish_usb_current(self):
        self.s['power_envelope']['available_current_ma']=3000.
        self.s['power_budget_policy']['loads'][0]['max_current_ma']=600.
        _,p,c=self.report(); self.assertIn('USB_PSD_DEFAULT_CURRENT_EXCESS',c)
        self.assertEqual(p['default_current_permission_a'],.5); self.assertIsNone(p['higher_current_permission_a'])
    def test_removed_budget_is_not_zero_load(self):
        self.s.pop('power_budget_policy'); _,p,c=self.report()
        self.assertIn('USB_PSD_BUDGET_INCOMPLETE',c); self.assertIsNone(p['declared_dc_reference_a'])
    def test_unbound_active_part_not_ignored(self):
        del self.s['part_bindings'][self.c('U1')['id']]
        self.assertIn('USB_PSD_BUDGET_INCOMPLETE',self.report()[2])
    def test_nc_text_cannot_hide_connected_data(self):
        data=next(p for p in self.c('J1')['pins'] if p['name']=='D+')
        self.move('J1',data['number'],'ANALOG_IN')
        self.assertEqual(self.report()[1]['role'],'unclassified')
    def test_missing_nc_intent_not_assumed(self):
        self.s['pin_intents']={}; self.assertIn('USB_PSD_TOPOLOGY_UNSUPPORTED',self.report()[2])
    def test_shared_cc_rejected(self):
        p=next(p for p in self.c('J1')['pins'] if p['name']=='CC2')
        self.move('J1',p['number'],'CC1'); self.assertEqual(self.report()[1]['role'],'unclassified')
    def test_cc_extra_load_rejected(self):
        self.move('C1','1','CC1'); self.assertEqual(self.report()[1]['role'],'unclassified')
    def test_unbound_rd_rejected(self):
        del self.s['part_bindings'][self.c('R1')['id']]; self.assertEqual(self.report()[1]['role'],'unclassified')
    def test_wrong_source_boundary_rejected(self):
        c=self.c('U1'); self.s['power_envelope']['positive_pin']=dict(component_id=c['id'],pin_id=c['pins'][0]['pin_id'])
        self.assertIn('USB_PSD_SOURCE_BOUNDARY',self.report()[2])
    def test_missing_standard_source_does_not_grant_permission(self):
        with patch.object(usb_psd,'evidence',return_value=[dict(available=False)]):
            _,p,c=self.report()
        self.assertIn('USB_PSD_SOURCE_UNAVAILABLE',c); self.assertIsNone(p['default_current_permission_a'])
    def test_source_status_changes_rule_hash(self):
        before=parts.rules_hash()
        with patch.object(usb_psd,'evidence',return_value=[dict(available=False)]):
            self.assertNotEqual(before,parts.rules_hash())
    def test_100nf_does_not_meet_1uf_initial_screen(self):
        self.s=copy.deepcopy(self.timer); _,p,c=self.report()
        self.assertIn('USB_PSD_DIRECT_CAP_MINIMUM',c)
        self.assertAlmostEqual(p['capacitance']['direct_initial_min_uf'],.09)
    def test_nominal_10uf_still_exceeds_with_tolerance(self):
        source=next(c for c in self.timer['components'].values() if c['ref']=='C1')
        target=self.c('C2'); cid=target['id']; oldpins=target['pins']
        replacement=copy.deepcopy(source); replacement.update(id=cid,ref='C2',pins=oldpins)
        self.s['components'][cid]=replacement
        for field in ['part_bindings','footprint_bindings']:
            self.s[field][cid]=copy.deepcopy(self.timer[field][source['id']])
        self.assertIn('USB_PSD_CAP_TOLERANCE_EXCESS',self.report()[2])
    def test_initial_cap_window_is_not_effective_cap_bound(self):
        _,p,c=self.report(); cap=p['capacitance']
        self.assertAlmostEqual(cap['direct_initial_min_uf'],1.89)
        self.assertAlmostEqual(cap['direct_initial_max_uf'],2.31)
        self.assertIsNone(cap['effective_exposed_min_uf']); self.assertIsNone(cap['effective_exposed_max_uf'])
    def test_fake_waiver_never_removes_hardware_errors(self):
        self.s['requirements'].append(dict(key='usb_compliant',value=True,origin='user_statement'))
        self.assertIn('USB_EXTERNAL_INPUT_BACKFEED_PATH',self.report()[2])
    def test_errors_reach_prototype_assessment(self):
        r=prototype_policy.assessment(Service.__new__(Service),self.s)
        self.assertEqual(r['verdict'],'fail')
        self.assertIn('USB_EXTERNAL_INPUT_BACKFEED_PATH',{f['code'] for f in r['findings'] if f['severity']=='error'})
    def test_controlled_mainboard_retains_separate_model(self):
        self.s=json.loads((R/'data/fb639744d29945359f393e331cfb34ab/state.json').read_text())
        q=usb_power.evaluate(self.s)
        self.assertFalse(any(f['severity']=='error' for f in q['findings']))
        self.assertNotIn('default_power',q['ports'][0]); self.assertEqual(q['verdict'],'incomplete')

if __name__=='__main__': unittest.main(verbosity=2)
