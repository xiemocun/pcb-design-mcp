"""Independent vendor bundles may reuse helper and parameter names safely."""
from pathlib import Path
import tempfile,unittest
from pcb_mcp import spice
from pcb_mcp.spice_bundle import isolate,read
from pcb_mcp.verification_models import VerificationModel
from test_verification_models import fixture


class Bundles(unittest.TestCase):
    def test_unresolved_dependency_duplicate_and_wrong_port_count_rejected(self):
        for text,code in [('.subckt top a b\nX1 a b missing\n.ends','DEPENDENCY_MISSING'),
                          ('.subckt top a b\nX1 a top\n.ends','PIN_COUNT'),
                          ('.subckt top a b\n.ends\n.subckt TOP a b\n.ends','DUPLICATE_SUBCIRCUIT')]:
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,code):isolate(spice.model_lines(text),'top',1)

    def test_dependencies_are_only_explicit_hashed_model_assets(self):
        raw=fixture();raw['dependency_assets']=['absent.lib']
        with self.assertRaisesRegex(ValueError,'DEPENDENCY_ASSET_REQUIRED'):VerificationModel.model_validate(raw)
        raw['dependency_assets']=[raw['model_asset']]
        with self.assertRaisesRegex(ValueError,'DEPENDENCY_DUPLICATE'):VerificationModel.model_validate(raw)

    def test_each_asset_folds_its_own_continuations_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);source=b'.subckt top a\n+ b\nX1 a b helper\n.ends\n';helper=b'.subckt helper a b\nR1 a b 1k\n.ends\n'
            (p/'main.lib').write_bytes(source);(p/'dependency.lib').write_bytes(helper)
            lines=read(dict(model_asset='main.lib',dependency_assets=['dependency.lib']),p)
            result,name=isolate(lines,'top',1);self.assertEqual(name,'mcp_scope_1')
            self.assertIn('.subckt top a b',result);self.assertEqual((p/'main.lib').read_bytes(),source)
            (p/'dependency.lib').write_text('+ b\n')
            with self.assertRaisesRegex(ValueError,'ORPHAN_CONTINUATION'):read(dict(model_asset='main.lib',dependency_assets=['dependency.lib']),p)

    @unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native solver unavailable')
    def test_native_independent_vendor_names_and_global_parameters_do_not_collide(self):
        def bundle(scale,index):
            source=f'.param factor={scale}\n.subckt same a b g\nX1 a b g helper\n.ends\n.subckt helper a b g\nE1 b g VALUE={{factor*V(a,g)}}\n.ends\n'
            return isolate(spice.model_lines(source),'same',index)
        first,n1=bundle(2,1);second,n2=bundle(3,2)
        deck='Independent source scopes\nV1 in 0 1\nX1 in a 0 '+n1+'\nX2 in b 0 '+n2+'\nR1 a 0 1000\nR2 b 0 1000\n'+'\n'.join(first+second)+'\n.op\n.end\n'
        with tempfile.TemporaryDirectory() as d:
            _,w=spice.run(deck,['v(a)','v(b)'],Path(d)/'run')
            self.assertAlmostEqual(float(w['v(a)'][0]),2.);self.assertAlmostEqual(float(w['v(b)'][0]),3.)


if __name__=='__main__':unittest.main()
