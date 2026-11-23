"""Native regression for UUID-dependent Specctra routing input."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from pcb_mcp import pcb_service
from pcb_mcp.revision_routing import order_seed_copper


class SeedCopperOrderTests(unittest.TestCase):
    def test_orders_inherited_records_without_changing_other_dsn_sections(self):
        header = '(pcb "pcb-mcp"\n  (parser (string_quote "))\n  (network (net "Z net") (net "A net"))\n'
        wire_z = '    (wire (path F.Cu 200 1 2\n      3 4)(net "Z net")(type route))\n'
        wire_a = '    (wire (path F.Cu 200 5 6 7 8)(net "A net")(type protect))\n'
        via_z = '    (via "Via[0-1]" 3 4 (net "Z net")(type route))\n'
        via_a = '    (via "Via[0-1]" 7 8 (net "A net")(type route))\n'
        before = header + '  (wiring\n'
        after = '  )\n)\n'
        expected = before + wire_a + wire_z + via_a + via_z + after
        self.assertEqual(order_seed_copper(before + wire_z + wire_a + via_z + via_a + after), expected)
        self.assertEqual(order_seed_copper(expected), expected)


@unittest.skipUnless(pcb_service.PYTHON.is_file(), 'Native KiCad Python unavailable')
class RoutingOrderTests(unittest.TestCase):
    def test_independent_native_ids_export_the_same_dsn(self):
        script = '''
import sys
import uuid
from pathlib import Path
import pcbnew as k
sys.path.insert(0, sys.argv[1])
from pcb_worker import export_dsn

root = Path(sys.argv[2])

def vector(x, y):
    return k.VECTOR2I(k.FromMM(x), k.FromMM(y))

def native_export(folder, reverse):
    folder.mkdir()
    path = folder / 'unrouted.kicad_pcb'
    board = k.BOARD()
    board.SetCopperLayerCount(4)
    net = k.NETINFO_ITEM(board, 'TEST_NET')
    board.Add(net)
    footprints = []
    for ref, x in [('J1', 5), ('J2', 15), ('J3', 25)]:
        fp = k.FOOTPRINT(board)
        fp.SetReference(ref)
        fp.SetFPID(k.LIB_ID('Test', 'Pad'))
        fp.SetPosition(vector(x, 8))
        pad = k.PAD(fp)
        pad.SetNumber('1')
        pad.SetSize(vector(1, 1))
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        pad.SetLayerSet(layers)
        pad.SetPosition(vector(x, 8))
        pad.SetNet(net)
        fp.Add(pad)
        board.Add(fp)
        footprints.append(fp)
    tracks = []
    for start, end in [(5, 15), (15, 25)]:
        track = k.PCB_TRACK(board)
        track.SetStart(vector(start, 8))
        track.SetEnd(vector(end, 8))
        track.SetWidth(k.FromMM(.2))
        track.SetLayer(k.F_Cu)
        track.SetNet(net)
        board.Add(track)
        tracks.append(track)
    for start, end in [((0, 0), (30, 0)), ((30, 0), (30, 16)),
                       ((30, 16), (0, 16)), ((0, 16), (0, 0))]:
        edge = k.PCB_SHAPE(board)
        edge.SetShape(k.SHAPE_T_SEGMENT)
        edge.SetStart(vector(*start))
        edge.SetEnd(vector(*end))
        edge.SetLayer(k.Edge_Cuts)
        board.Add(edge)
    ordered = footprints + tracks
    for i, item in enumerate(reversed(ordered) if reverse else ordered, 1):
        item.SetUuid(k.KIID(str(uuid.UUID(int=i + (100 if reverse else 0)))))
    board.BuildListOfNets()
    assert k.SaveBoard(str(path), board)
    path.with_suffix('.kicad_pro').write_text('{"meta":{"version":1}}')
    path.with_suffix('.kicad_dru').write_text('(version 1)\\n')
    board = k.LoadBoard(str(path))
    refs = [fp.GetReference() for fp in board.GetFootprints()]
    originals = {f: f.read_bytes() for f in folder.iterdir()}
    export_dsn(board, folder, {'clearance_mm': .15})
    assert all(f.read_bytes() == value for f, value in originals.items())
    dsn = (folder / 'input.dsn').read_bytes()
    assert b'TEST_NET' in dsn and b'In2.Cu' in dsn and b'(wiring' in dsn
    return refs, dsn

first_order, first = native_export(root / 'first', False)
second_order, second = native_export(root / 'second', True)
assert first_order != second_order, (first_order, second_order)
assert first == second
'''
        with tempfile.TemporaryDirectory() as temporary:
            process = subprocess.run(
                [str(pcb_service.PYTHON), '-c', script, str(Path(pcb_service.__file__).parent), temporary],
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(process.returncode, 0, process.stderr)
