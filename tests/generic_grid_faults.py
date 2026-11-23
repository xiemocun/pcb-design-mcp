"""Count thresholds, real size constraints, and finite capacity rejection."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pcb_mcp.generic_placement import grid_shape
class Grid(unittest.TestCase):
 def test_added_small_part_does_not_force_unfit_column(self):
  sizes=[(12.31,3.05),(10.69,8.99)]+[(3.01,1.51)]*11
  cols,rows=grid_shape(13,90.,70.,5.,sizes)
  self.assertEqual((cols,rows),(4,4))
  self.assertTrue(all(x+4<=80/cols and y+4<=60/rows for x,y in sizes))
 def test_fitting_original_grid_retained(self):
  self.assertEqual(grid_shape(12,90.,70.,5.,[(12.31,3.05),(10.69,8.99)]+[(3.01,1.51)]*10),(4,3))
 def test_too_large_footprint_still_rejected(self):
  with self.assertRaisesRegex(ValueError,'PLACEMENT_CAPACITY_EXCEEDED'):grid_shape(1,90.,70.,5.,[(81.,8.)])
 def test_total_capacity_still_rejected(self):
  with self.assertRaisesRegex(ValueError,'PLACEMENT_CAPACITY_EXCEEDED'):grid_shape(13,40.,40.,5.,[(14.,8.)]*13)
 def test_missing_geometry_rejected(self):
  with self.assertRaisesRegex(ValueError,'PLACEMENT_GEOMETRY_REQUIRED'):grid_shape(13,90.,70.,5.,[(14.,8.)])
if __name__=='__main__':unittest.main(verbosity=2)
