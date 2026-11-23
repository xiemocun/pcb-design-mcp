"""Source-package preparation refuses path escape and counterfeit PDF bodies."""
import json
from pathlib import Path
import tempfile
import unittest
from pcb_mcp_client.packages import materialize


class Packages(unittest.TestCase):
    def test_concat_cannot_hide_a_parent_path(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'nested').mkdir();(root/'outside.lib').write_text('source\n')
            p=root/'nested/spec.json';p.write_text(json.dumps(dict(assets=[dict(path='model.lib',kind='spice_model',files=['../outside.lib'])])))
            with self.assertRaisesRegex(ValueError,'OUTSIDE_PACKAGE'):materialize(p,'model')

    def test_concat_cannot_merge_two_spice_statements(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'a.lib').write_text('.ends sample');(root/'b.lib').write_text('.subckt next a b\n')
            p=root/'spec.json';p.write_text(json.dumps(dict(assets=[dict(path='model.lib',kind='spice_model',files=['a.lib','b.lib'])])))
            with self.assertRaisesRegex(ValueError,'REQUIRES_NEWLINE'):materialize(p,'model')

    def test_html_cannot_be_packaged_as_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'document.pdf').write_text('<html>redirect shell</html>')
            (root/'spec.json').write_text(json.dumps(dict(assets=[dict(path='document.pdf',kind='document',file='document.pdf')])))
            with self.assertRaisesRegex(ValueError,'NOT_COMPLETE_PDF'):materialize(root/'spec.json','catalog')

    def test_local_package_cannot_read_parent_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'nested').mkdir();(root/'outside.txt').write_text('outside')
            p=root/'nested/spec.json';p.write_text(json.dumps(dict(assets=[dict(path='doc.txt',kind='document',file='../outside.txt')])))
            with self.assertRaisesRegex(ValueError,'OUTSIDE_PACKAGE'):materialize(p,'catalog')

    def test_snapshot_hash_cannot_be_silently_changed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'document.txt').write_text('actual source')
            p=root/'spec.json';p.write_text(json.dumps(dict(assets=[dict(path='doc.txt',kind='document',file='document.txt',sha256='0'*64)])))
            with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):materialize(p,'catalog')


if __name__=='__main__':unittest.main()
