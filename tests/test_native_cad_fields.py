import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pcb_mcp import backend, parts


class NativeCADSourceFieldsTests(unittest.TestCase):
    def setUp(self):
        self.part = dict(candidate_id='a' * 32, evidence_status='available',
            manufacturer='Example "Source"', mpn='TEST-IDENTITY', sources=[dict(url='https://example.invalid/data')])
        self.state = dict(components={'c': dict(id='c', ref='U1')}, part_bindings={'c': self.part})

    def test_source_identity_only_no_invented_ratings(self):
        with patch.object(parts, 'catalog', return_value=[self.part]), patch.object(parts, 'binding_valid', return_value=True):
            fields = parts.native_cad_fields(self.state)
        self.assertEqual(fields, {'U1': dict(Manufacturer=self.part['manufacturer'], MPN='TEST-IDENTITY', SourceURL='https://example.invalid/data')})

    def test_stale_source_or_inconsistent_binding_refused(self):
        for available, valid in [([], True), ([self.part], False)]:
            with self.subTest(valid=valid, source_present=bool(available)), patch.object(parts, 'catalog', return_value=available), patch.object(parts, 'binding_valid', return_value=valid):
                with self.assertRaisesRegex(ValueError, 'NATIVE_CAD_PART_IDENTITY_INVALID'):
                    parts.native_cad_fields(self.state)

    def test_unbound_geometry_draft_gets_no_source_claim(self):
        state = copy.deepcopy(self.state)
        state['part_bindings'] = {}
        with patch.object(parts, 'catalog', return_value=[]):
            self.assertEqual(parts.native_cad_fields(state), {})

    def test_actual_native_xml_missing_identity_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.xml'
            path.write_text('<export><components><comp ref="U1"><fields><field name="MPN">TEST-IDENTITY</field></fields></comp></components></export>')
            with patch.object(parts, 'native_cad_fields', return_value={'U1': {'MPN': 'TEST-IDENTITY', 'Manufacturer': 'source'}}):
                report = backend.native_source_field_report(self.state, path)
            self.assertFalse(report['matched'])
            self.assertEqual(report['mismatch_references'], ['U1'])


if __name__ == '__main__':
    unittest.main()
