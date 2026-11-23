import unittest,copy
from types import SimpleNamespace
from pcb_mcp.catalog_import import validate_variants
from pcb_mcp.common import DomainError
class VariantTests(unittest.TestCase):
 def setUp(self):
  self.base=dict(manufacturer='Vendor',mpn='P',kind='mcu',symbol_id='L:P',footprint_id='L:original',definition_hash='a'*64,facts=dict(symbol_definition_hash='b'*64,footprint_definition_hash='c'*64,pin_mapping={'1':'GND'},supply_max_v=3.6))
  self.part=SimpleNamespace(manufacturer='Vendor',mpn='P',kind='mcu',symbol_id='L:P',footprint_id='L:new',symbol_definition_hash='b'*64,footprint_definition_hash='d'*64,pin_mapping={'1':'GND'},facts={'supply_max_v':3.6},footprint_variant_of='a'*64)
 def test_explicit_identical_electrical_variant_adds_without_mutation(self):
  before=copy.deepcopy(self.base);validate_variants([self.part],[self.base]);self.assertEqual(before,self.base)
 def test_unbound_duplicate_rejected(self):
  self.part.footprint_variant_of=None
  with self.assertRaisesRegex(DomainError,'IMPORT_MPN_CONFLICT'):validate_variants([self.part],[self.base])
 def test_electrical_fact_and_pin_changes_rejected(self):
  self.part.facts['supply_max_v']=5.
  with self.assertRaisesRegex(DomainError,'IMPORT_VARIANT_ELECTRICAL_CHANGE'):validate_variants([self.part],[self.base])
  self.part.facts['supply_max_v']=3.6;self.part.pin_mapping={'1':'VDD'}
  with self.assertRaisesRegex(DomainError,'IMPORT_VARIANT_ELECTRICAL_CHANGE'):validate_variants([self.part],[self.base])
 def test_wrong_base_rejected(self):
  self.part.footprint_variant_of='e'*64
  with self.assertRaisesRegex(DomainError,'IMPORT_VARIANT_BASE_MISSING'):validate_variants([self.part],[self.base])
 def test_original_footprint_cannot_be_overwritten(self):
  self.part.footprint_id='L:original'
  with self.assertRaisesRegex(DomainError,'IMPORT_VARIANT_NOT_DISTINCT'):validate_variants([self.part],[self.base])
if __name__=='__main__':unittest.main()
