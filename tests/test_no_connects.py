"""NC native names preserve isolation and cannot hide accidental real shorts."""
import copy,tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from pcb_mcp import no_connects
from pcb_mcp.copper import dsn_report
from pcb_mcp.common import DomainError


class NoConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'actual.xml'
        self.state=dict(components={'u':dict(id='u',ref='U1',pins=[dict(number='1',pin_id='nc'),dict(number='2',pin_id='p')])},pin_intents={'u:nc':dict(intent='no_connect',pin=dict(component_id='u',pin_id='nc'))})
        self.xml=ET.fromstring('<export><nets><net name="unconnected-(U1-NC-Pad1)"><node ref="U1" pin="1" pintype="no_connect"/></net><net name="VCC"><node ref="U1" pin="2"/></net></nets></export>')
    def tearDown(self):self.temp.cleanup()
    def read(self):ET.ElementTree(self.xml).write(self.path);return no_connects.read(self.state,self.path)
    def test_actual_native_name_is_preserved_as_a_single_isolated_pad(self):
        r=self.read();self.assertEqual(no_connects.pads(r),{('U1','1'):'unconnected-(U1-NC-Pad1)'})
    def test_board_netlist_preserves_kicad_escaped_pin_name(self):
        net = self.xml.find('./nets/net')
        net.set('name', 'unconnected-(U1-TRIM/NR-Pad1)')
        self.assertEqual(no_connects.pads(self.read())[('U1','1')], net.get('name'))
        native = self.path.with_suffix('.net')
        native.write_text('(export (nets (net (name "unconnected-(U1-TRIM{slash}NR-Pad1)") '
                          '(node (ref "U1") (pin "1")))))')
        self.assertEqual(no_connects.pads(no_connects.read_board_names(self.state, native)),
                         {('U1','1'):'unconnected-(U1-TRIM{slash}NR-Pad1)'})
    def test_unmarked_isolated_pin_is_not_silently_accepted(self):
        self.state['pin_intents'].clear()
        with self.assertRaisesRegex(ValueError,'NOT_DECLARED'):self.read()
    def test_nc_connected_to_a_real_power_pin_is_rejected(self):
        ET.SubElement(self.xml.find('./nets/net'),'node',dict(ref='U1',pin='2'))
        with self.assertRaisesRegex(ValueError,'NOT_DECLARED'):self.read()
    def test_two_independent_nc_pins_cannot_be_joined(self):
        self.state['pin_intents']['u:p']=dict(intent='no_connect',pin=dict(component_id='u',pin_id='p'));ET.SubElement(self.xml.find('./nets/net'),'node',dict(ref='U1',pin='2'))
        with self.assertRaisesRegex(ValueError,'JOINS_INDEPENDENT'):self.read()
    def test_missing_native_nc_net_cannot_be_treated_as_an_empty_pad(self):
        self.xml.find('nets').remove(self.xml.find('./nets/net'))
        with self.assertRaisesRegex(ValueError,'NET_MISSING'):self.read()
    def test_duplicate_nc_node_does_not_collapse_to_one_physical_pin(self):
        ET.SubElement(self.xml.find('./nets/net'),'node',dict(ref='U1',pin='1'))
        with self.assertRaisesRegex(ValueError,'DUPLICATED'):self.read()
    def dsn(self):
        return '(pcb "pcb-mcp" (unit um) (structure (layer F.Cu) (layer B.Cu)) (network (class Default VCC "unconnected-(U1-NC-Pad1)" (rule (width 200)))))'
    def routing_state(self):return dict(nets={'p':dict(name='VCC')},board_constraints=dict(layers=2,track_width_mm=.2))
    def test_native_nc_identity_is_checked_in_the_router_net_classes(self):
        r=dsn_report(self.routing_state(),self.dsn(),self.read());self.assertEqual(r['actual_mm']['unconnected-(U1-NC-Pad1)'],.2)
    def test_an_unregistered_extra_router_net_is_still_rejected(self):
        with self.assertRaises(DomainError):dsn_report(self.routing_state(),self.dsn())
    def test_nc_width_corruption_is_still_rejected(self):
        with self.assertRaises(DomainError):dsn_report(self.routing_state(),self.dsn().replace('width 200','width 100'),self.read())


if __name__=='__main__':unittest.main()
