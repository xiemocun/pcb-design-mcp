"""Tamper, identity, pin-coverage and static-logic model failures."""
import base64,copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pcb_mcp import registry
from pcb_mcp.verification_models import VerificationModel,install,packages
from pcb_mcp.digital_truth import validate_model


def asset(path,kind,text):
    data=text.encode();return dict(path=path,kind=kind,source_url='https://example.test/synthetic-fixture',sha256=registry.sha(data),content_base64=base64.b64encode(data).decode())


def fixture():
    return dict(name='2test',part_definition_hash='a'*64,engine='spice_subcircuit',pins=['1','2'],assets=[asset('docs/source.txt','document','Synthetic unit test evidence; no manufacturer qualification'),asset('models/test.lib','spice_model','.subckt 2test 1 2\nR1 1 2 1000\n.ends 2test')],evidence=[dict(asset='docs/source.txt',locator='Synthetic test fixture only',interpretation='Two physical terminals for parser and binding tests only.')],model_asset='models/test.lib',temperature_range_c=[25.,25.],conditions='Synthetic fixture at25C for software tests only.')


class VerificationModelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.context=registry.using(self.temp.name);self.context.__enter__()
        self.catalog=patch('pcb_mcp.parts.catalog',return_value=[dict(definition_hash='a'*64,evidence_status='available',symbol_id='Device:R')]);self.catalog.start()
        self.symbols=patch('pcb_mcp.backend.catalog',return_value=[dict(library_id='Device:R',pins=[dict(number='1'),dict(number='2')])]);self.symbols.start()
    def tearDown(self):self.symbols.stop();self.catalog.stop();self.context.__exit__(None,None,None);self.temp.cleanup()
    def test_immutable_idempotent_model_with_numeric_name(self):
        req=VerificationModel.model_validate(fixture());first=install(req);second=install(req)
        self.assertEqual(first['model_id'],second['model_id']);self.assertTrue(second['already_imported'])
    def test_absent_new_static_capability_preserves_legacy_manifest_identity(self):
        req=VerificationModel.model_validate(fixture());manifest=req.model_dump(mode='json');manifest.pop('static_supply_current');manifest.pop('dc_limits');manifest.pop('temperature_resistance');manifest.pop('spice_units');manifest.pop('behavioral_temperature');manifest.pop('dependency_assets');manifest.pop('spice_noise_scope')
        for a in manifest['assets']:a.pop('content_base64')
        self.assertEqual(install(req)['model_id'],registry.sha(registry.canonical(manifest)))
    def test_content_hash_not_trusted_without_bytes(self):
        raw=fixture();raw['assets'][1]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):install(VerificationModel.model_validate(raw))
    def test_unknown_part_cannot_acquire_model(self):
        raw=fixture();raw['part_definition_hash']='b'*64
        with self.assertRaisesRegex(ValueError,'IDENTITY_UNAVAILABLE'):install(VerificationModel.model_validate(raw))
    def test_missing_physical_pin_fails(self):
        raw=fixture();raw['pins']=['1']
        with self.assertRaisesRegex(ValueError,'PIN_COVERAGE'):install(VerificationModel.model_validate(raw))
    def test_subcircuit_arity_mismatch_fails(self):
        raw=fixture();raw['assets'][1]=asset('models/test.lib','spice_model','.subckt 2test 1 2 3\nR1 1 2 1000\n.ends')
        with self.assertRaisesRegex(ValueError,'PIN_COUNT'):install(VerificationModel.model_validate(raw))

    def test_transmission_line_import_retains_data_and_requires_real_entry_name(self):
        raw=fixture();raw['assets'][1]=asset('models/test.lib','spice_model','.subckt 2test 1 2\nT1 1 0 2 0 Z0=50 TD=39n\n.ends 2test')
        result=install(VerificationModel.model_validate(raw))
        stored=Path(self.temp.name)/'verification_models'/result['model_id']/'models/test.lib'
        self.assertEqual(stored.read_text(),'.subckt 2test 1 2\nT1 1 0 2 0 Z0=50 TD=39n\n.ends 2test')
        raw['name']='friendly_label_that_is_not_the_subcircuit'
        with self.assertRaisesRegex(ValueError,'ENTRY_NOT_UNIQUE'):install(VerificationModel.model_validate(raw))

    def test_parameter_defaults_do_not_become_physical_ports(self):
        raw=fixture();raw['assets'][1]=asset('models/test.lib','spice_model','.subckt 2test 1 2 params: r=1000\nR1 1 2 {r}\n.ends')
        self.assertIn('model_id',install(VerificationModel.model_validate(raw)))
        raw['pins']=['1']
        with self.assertRaisesRegex(ValueError,'PIN_COVERAGE'):install(VerificationModel.model_validate(raw))
    def test_modified_model_file_rejected_after_restart(self):
        result=install(VerificationModel.model_validate(fixture()));path=Path(self.temp.name)/'verification_models'/result['model_id']/'models/test.lib';path.write_text('changed')
        with self.assertRaisesRegex(ValueError,'ASSET_CHANGED'):packages()
    def test_arbitrary_control_not_accepted_as_model(self):
        raw=fixture();raw['assets'][1]=asset('models/test.lib','spice_model','.control\nshell bad\n.endc')
        with self.assertRaisesRegex(ValueError,'DIRECTIVE_NOT_ALLOWED'):install(VerificationModel.model_validate(raw))
    def test_path_traversal_rejected(self):
        raw=fixture();raw['assets'][1]['path']='../model'
        with self.assertRaisesRegex(ValueError,'ASSET_PATH'):VerificationModel.model_validate(raw)
    def test_digital_truth_requires_all_rows_and_thresholds(self):
        model=dict(pins=['1','2','3','4'],supply_range_v=[3.,3.],truth=dict(inputs=['1'],outputs=['2'],supply=['3','4'],rows={'0':'1','1':'0'},vil_max_v=.8,vih_min_v=2.,vol_max_v=.1,voh_min_v=2.9,input_leakage_max_a=1e-6,output_current_max_a=.001))
        validate_model(model);bad=copy.deepcopy(model);bad['truth']['rows'].pop('1')
        with self.assertRaisesRegex(ValueError,'INCOMPLETE'):validate_model(bad)
        bad=copy.deepcopy(model);bad['truth']['voh_min_v']=.05
        with self.assertRaisesRegex(ValueError,'THRESHOLD_ORDER'):validate_model(bad)


if __name__=='__main__':unittest.main()
