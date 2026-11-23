"""Real KiCad geometry regression: holes versus actual mask openings."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path('src').resolve()))
import pcbnew as k
from pcb_mcp.assembly_geometry import extract
class MaskTests(unittest.TestCase):
 def test_original_holes_remain_detected(self):
  b=k.LoadBoard('work/riding-intercom/vendor-review/CAD/PCB/routed.kicad_pcb');self.assertEqual(len(extract(b,k)['fill_cap_holes']),19)
 def test_grid_preserves_thermal_vias_and_detects_remaining_seven(self):
  b=k.LoadBoard('work/riding-intercom/cost-down/workbench/cost01.kicad_pcb');r=extract(b,k);self.assertEqual(len(r['fill_cap_holes']),7)
  self.assertEqual(sum(p['drill_x_mm']>0 for p in r['components']['U1']['pads']),12)
  self.assertEqual(r['components']['U1']['assembly_method'],'SMT')
  self.assertTrue(all(r['components'][f'J{i}']['assembly_method']=='THT/manual after reflow' for i in range(1,8)))
 def test_regression_opening_thermal_copper_restores_all_twelve_flags(self):
  b=k.LoadBoard('work/riding-intercom/cost-down/workbench/cost01.kicad_pcb');u=next(f for f in b.GetFootprints() if f.GetReference()=='U1');p=next(p for p in u.Pads() if p.GetNumber()=='41' and p.GetAttribute()==k.PAD_ATTRIB_SMD);ls=p.GetLayerSet();ls.AddLayer(k.F_Mask);p.SetLayerSet(ls);self.assertEqual(len(extract(b,k)['fill_cap_holes']),19)
if __name__=='__main__':unittest.main()
