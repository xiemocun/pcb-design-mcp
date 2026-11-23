"""Fault coverage for immutable catalog extension. Documents here are synthetic."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from pcb_mcp import backend, footprints, parts, registry
from pcb_mcp.catalog_import import CatalogPackage, install
from pcb_mcp.common import DomainError, write_json
from pcb_mcp.models import BuildRef, Search, FootprintSearch, PartSearch, Candidate
from pcb_mcp.service import Service


def asset(path, content, kind='document'):
    return dict(path=path, kind=kind, source_url='https://example.invalid/synthetic-test-only',
                sha256=registry.sha(content), content_base64=base64.b64encode(content).decode())


def package(symbol, footprint):
    evidence = [dict(asset='documents/test.txt', locator='Synthetic test section 1', interpretation='Test fixture only, not manufacturer data or a validated part.')]
    return dict(name='EXT_TEST_FET', description='Synthetic package for importer fault tests only', assets=[asset('documents/test.txt', b'Synthetic test document, not engineering evidence.')],
                parts=[dict(manufacturer='TEST ONLY', mpn='TEST_ONLY_FET', kind='mosfet', symbol_id=symbol['library_id'],
                            footprint_id=footprint['footprint_id'], symbol_definition_hash=symbol['definition_hash'],
                            footprint_definition_hash=footprint['definition_hash'], pin_mapping={'1': 'G', '2': 'S', '3': 'D'},
                            facts={}, evidence={key: evidence for key in ('identity', 'pin_mapping', 'package')})])


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.symbol = next(c for c in backend.catalog(['Transistor_FET']) if c['symbol'] == '2N7002')
        cls.footprint = footprints.load(*footprints.entries('Package_TO_SOT_SMD:SOT-23', 'Package_TO_SOT_SMD')[0])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = package(self.symbol, self.footprint)

    def install(self):
        return install(CatalogPackage.model_validate(self.raw), self.root)

    def test_new_family_query_survives_process_recreation(self):
        result = self.install()
        service = Service(self.root)
        found = service.parts_search(PartSearch(query='TEST_ONLY_FET', kind='mosfet'))
        self.assertEqual(found['total'], 1)
        self.assertEqual(found['candidates'][0]['evidence_status'], 'available')
        symbol = service.search(Search(query='Transistor_FET:2N7002', library='Transistor_FET'))['candidates'][0]
        service.get_candidate(Candidate(candidate_id=symbol['candidate_id']))
        second = Service(self.root)
        self.assertEqual(second.candidate(symbol['candidate_id'])['definition_hash'], self.symbol['definition_hash'])
        self.assertTrue(result['installed'])

    def test_catalog_is_loaded_on_symbol_lookup_and_skipped_for_project_list(self):
        self.install()
        with patch('pcb_mcp.backend.catalog', wraps=backend.catalog) as catalog:
            service = Service(self.root)
            self.assertEqual(service.list_projects(), {'projects': []})
            catalog.assert_not_called()
            resolved = service.get_candidate(Candidate(candidate_id=self.symbol['candidate_id']))
            self.assertEqual(resolved['definition_hash'], self.symbol['definition_hash'])
            catalog.assert_called_once_with()

    def test_idempotent_import_preserves_bundle_bytes(self):
        result = self.install()
        path = self.root / 'catalog_packages' / result['package_id'] / 'manifest.json'
        before = path.read_bytes()
        self.assertFalse(self.install()['installed'])
        self.assertEqual(path.read_bytes(), before)

    def test_data_roots_are_isolated(self):
        self.install()
        with registry.using(self.root): self.assertIn('TEST_ONLY_FET', {p['mpn'] for p in parts.catalog()})
        with tempfile.TemporaryDirectory() as other, registry.using(other):
            self.assertNotIn('TEST_ONLY_FET', {p['mpn'] for p in parts.catalog()})

    def test_builtin_identities_do_not_change_on_import(self):
        with registry.using(self.root): before = parts.catalog()
        self.install()
        with registry.using(self.root): self.assertEqual(parts.catalog()[:len(before)], before)

    def test_unrelated_import_keeps_build_but_missing_selected_part_invalidates_it(self):
        selected_package = self.install()['package_id']
        service = Service(self.root)
        project_id, build_id = 'a' * 32, 'b' * 32
        folder = self.root / project_id / 'builds' / build_id
        folder.mkdir(parents=True)
        schematic = b'retained schematic fixture'
        (folder / 'led.kicad_sch').write_bytes(schematic)
        request = BuildRef(project_id=project_id, build_id=build_id)
        with service.locked():
            selected = next(p for p in parts.catalog() if p['mpn'] == 'TEST_ONLY_FET')
            snapshot = folder / 'rulepacks.json'
            write_json(snapshot, service.rules_list())
            state = dict(id=project_id, revision=1,
                         components={'q': dict(id='q', ref='Q1', library_id=self.symbol['library_id'],
                                               symbol='2N7002', value='2N7002')},
                         part_bindings={'q': selected}, footprint_bindings={'q': self.footprint},
                         builds={build_id: dict(id=build_id, revision=1, schematic_hash=registry.sha(schematic),
                                                source_hashes={'rulepacks.json': registry.sha(snapshot.read_bytes())})})
            service.save(state)
            service.target(request)
        self.raw['name'] = 'EXT_UNRELATED_FET'
        self.raw['parts'][0]['mpn'] = 'UNRELATED_FET'
        self.install()
        with service.locked():
            service.target(request)
            changed = parts.catalog()
            changed[0]['evidence_status'] = 'missing_or_changed'
            with patch.object(parts, 'catalog', return_value=changed):
                with self.assertRaisesRegex(DomainError, 'RULEPACK_CHANGED'):
                    service.target(request)
            (self.root / 'catalog_packages' / selected_package).rename(self.root / 'removed-source-package')
            with self.assertRaisesRegex(DomainError, 'BUILD_PART_EVIDENCE_CHANGED'):
                service.target(request)

    def test_wrong_asset_hash_rejected_without_commit(self):
        self.raw['assets'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(DomainError, 'IMPORT_ASSET_HASH_MISMATCH'): self.install()
        self.assertFalse(list((self.root / 'catalog_packages').glob('[a-f0-9]*')))

    def test_wrong_pin_map_rejected(self):
        self.raw['parts'][0]['pin_mapping']['4'] = 'invented'
        with self.assertRaisesRegex(DomainError, 'IMPORT_PIN_MAP_MISMATCH'): self.install()

    def test_wrong_symbol_hash_rejected(self):
        self.raw['parts'][0]['symbol_definition_hash'] = '0' * 64
        with self.assertRaisesRegex(DomainError, 'IMPORT_SYMBOL_CHANGED'): self.install()

    def test_swapped_gate_source_rejected(self):
        self.raw['parts'][0]['pin_mapping'].update({'1': 'S', '2': 'G'})
        with self.assertRaisesRegex(DomainError, 'IMPORT_PIN_ROLE_MISMATCH'): self.install()

    def test_wrong_footprint_hash_rejected(self):
        self.raw['parts'][0]['footprint_definition_hash'] = '0' * 64
        with self.assertRaisesRegex(DomainError, 'IMPORT_FOOTPRINT_CHANGED'): self.install()

    def test_fact_without_source_rejected(self):
        self.raw['parts'][0]['facts']['voltage_abs_max_v'] = 1000
        with self.assertRaisesRegex(ValueError, 'EACH_FACT_REQUIRES_EVIDENCE'): self.install()

    def test_pin_fact_cannot_override_checked_mapping(self):
        self.raw['parts'][0]['facts']['pin_mapping'] = {'1': 'wrong'}
        with self.assertRaisesRegex(ValueError, 'RESERVED_FACT_NAME'): self.install()

    def test_absolute_and_traversal_paths_rejected(self):
        for path in ('../escape.txt', '/tmp/escape.txt', 'a/../../escape', 'a\\b', 'manifest.json'):
            with self.subTest(path=path):
                self.raw['assets'][0]['path'] = path
                with self.assertRaises(ValueError): self.install()

    def test_library_traversal_rejected(self):
        for model in (Search, FootprintSearch):
            with self.assertRaises(ValueError): model(query='x', library='../Device')
        with self.assertRaisesRegex(ValueError, 'INVALID_LIBRARY_NAME'): backend.catalog(['../Device'])

    def test_non_https_source_rejected(self):
        self.raw['assets'][0]['source_url'] = 'file:///private/source'
        with self.assertRaisesRegex(ValueError, 'HTTPS_SOURCE_URL_REQUIRED'): self.install()

    def test_same_mpn_cannot_be_shadowed(self):
        self.install()
        self.raw['name'] = 'EXT_OTHER'
        with self.assertRaisesRegex(DomainError, 'IMPORT_MPN_CONFLICT'): self.install()

    def test_same_mpn_different_manufacturers_remain_distinct(self):
        self.install()
        self.raw['name']='EXT_SECOND_VENDOR'
        self.raw['parts'][0]['manufacturer']='SECOND TEST VENDOR'
        self.install()
        with registry.using(self.root):
            found=[p for p in parts.catalog() if p['mpn']=='TEST_ONLY_FET']
        self.assertEqual(len(found),2)
        self.assertEqual(len({p['candidate_id'] for p in found}),2)
        from pcb_mcp_client.plan import select_part
        self.assertEqual(select_part(found,'TEST_ONLY_FET',found[1]['definition_hash']),found[1])
        with self.assertRaisesRegex(ValueError,'PART_EVIDENCE_CHANGED'):
            select_part(found,'TEST_ONLY_FET','0'*64)

    def test_import_cannot_shadow_legacy_model_ratings(self):
        original=parts.reference_catalog()
        basis=next(p for p in original if p['symbol_id']=='RF_Module:ESP32-WROOM-32E')
        changed=copy.deepcopy(basis)
        changed['mpn']='TEST ONLY ALTERNATE PART'
        changed['facts'].update(import_record={'synthetic':True},supply_max_v=999.0)
        state={'components':{'c':{'id':'c','library_id':basis['symbol_id']}},'part_bindings':{'c':changed}}
        with patch.object(parts,'catalog',return_value=original+[changed]):
            self.assertEqual(parts.reference_catalog(),original)
            self.assertEqual(parts.reference_binding_findings(state)[0]['code'],'REFERENCE_MODEL_IDENTITY_MISMATCH')

    def test_tampered_document_breaks_lookup_and_idempotence(self):
        result = self.install()
        file = self.root / 'catalog_packages' / result['package_id'] / 'documents/test.txt'
        file.write_text('changed')
        with registry.using(self.root), self.assertRaisesRegex(ValueError, 'CATALOG_ASSET_CHANGED'): parts.catalog()
        with self.assertRaisesRegex(ValueError, 'CATALOG_ASSET_CHANGED'): self.install()

    def test_tampered_manifest_rejected(self):
        result = self.install()
        file = self.root / 'catalog_packages' / result['package_id'] / 'manifest.json'
        value = json.loads(file.read_text())
        value['parts'][0]['mpn'] = 'OTHER'
        file.write_text(json.dumps(value))
        with registry.using(self.root), self.assertRaisesRegex(ValueError, 'CATALOG_PACKAGE_CHANGED'): parts.catalog()

    def test_custom_symbol_and_footprint_namespace(self):
        name = self.raw['name']
        definition = self.symbol['definition']
        raw_symbol = '(kicad_symbol_lib (version 20250114) (generator "test") ' + definition + ')'
        self.raw['assets'].append(asset('symbols/' + name + '.kicad_sym', raw_symbol.encode(), 'symbol'))
        raw_footprint = Path(self.footprint['source']).read_bytes()
        self.raw['assets'].append(asset('footprints/' + name + '.pretty/SOT-23.kicad_mod', raw_footprint, 'footprint'))
        self.raw['parts'][0]['symbol_id'] = name + ':2N7002'
        self.raw['parts'][0]['footprint_id'] = name + ':SOT-23'
        self.install()
        with registry.using(self.root):
            found = next(c for c in backend.catalog([name]) if c['symbol'] == '2N7002')
            self.assertNotEqual(found['candidate_id'], self.symbol['candidate_id'])
            self.assertEqual(found['definition_hash'], self.symbol['definition_hash'])
            self.assertEqual(parts.catalog()[-1]['evidence_status'], 'available')


if __name__ == '__main__':
    unittest.main()
