import itertools
from pathlib import Path
import subprocess
import unittest

from pcb_mcp.generic_placement import bounded_positions


class CompactPlacementTests(unittest.TestCase):
    def test_fallback_packs_real_footprints_within_unchanged_outline(self):
        # The previous uniform 4 mm allowance required three 9 mm shelves
        # in a 20 mm field, even though the footprints fit with a 1 mm gap.
        sizes = {ref: (16., 5.) for ref in ('J1', 'J2', 'J3')}
        positions = bounded_positions(sizes, 30., 30., 5.)
        self.assertEqual(positions, bounded_positions(dict(reversed(list(sizes.items()))), 30., 30., 5.))

        boxes = []
        for ref, (x, y) in positions.items():
            width, height = sizes[ref]
            left, top, right, bottom = (x-width/2, y-height/2, x+width/2, y+height/2)
            self.assertTrue(5. <= left < right <= 25.)
            self.assertTrue(5. <= top < bottom <= 25.)
            boxes.append((left, top, right, bottom))

        for a, b in itertools.combinations(boxes, 2):
            self.assertGreaterEqual(max(b[0]-a[2], a[0]-b[2], b[1]-a[3], a[1]-b[3]), 1.)

    def test_insufficient_area_still_fails(self):
        with self.assertRaisesRegex(ValueError, 'PLACEMENT_CAPACITY_EXCEEDED'):
            bounded_positions({ref: (12., 12.) for ref in ('U1', 'U2', 'U3')}, 30., 30., 5.)


KICAD = Path('/Applications/KiCad/KiCad.app/Contents')
KICAD_PYTHON = KICAD / 'Frameworks/Python.framework/Versions/3.9/bin/python3.9'


@unittest.skipUnless(KICAD_PYTHON.is_file(), 'Native KiCad footprint geometry unavailable')
class CourtyardPlacementTests(unittest.TestCase):
    def test_true_overlap_rejected_inside_nonrectangular_native_bounding_box(self):
        script = '''
import sys
import pcbnew as k
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from explicit_placement import native_check
root = Path(sys.argv[2])
board = k.BOARD()
state = {'board_constraints': {'width_mm': 75, 'height_mm': 55}}
for ref, library, name, x, y in [
    ('U1', 'RF_Module', 'ESP32-C3-WROOM-02', 34, 23),
    ('C1', 'Capacitor_SMD', 'C_0603_1608Metric', 22.6, 17),
]:
    fp = k.FootprintLoad(str(root / (library + '.pretty')), name)
    fp.SetReference(ref)
    fp.SetPosition(k.VECTOR2I(k.FromMM(x), k.FromMM(y)))
    board.Add(fp)
assert len(native_check(board, k, state)) == 2
for x, expected in [(24.7, 'EXPLICIT_BODY_OVERLAP'), (76, 'EXPLICIT_BODY_OUTSIDE')]:
    fp.SetPosition(k.VECTOR2I(k.FromMM(x), k.FromMM(17)))
    try:
        native_check(board, k, state)
    except ValueError as error:
        assert str(error).startswith(expected), error
    else:
        raise AssertionError(expected)
'''
        module = Path(__file__).resolve().parents[1] / 'src/pcb_mcp'
        run = subprocess.run([str(KICAD_PYTHON), '-c', script, str(module),
                              str(KICAD / 'SharedSupport/footprints')],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
