import unittest
from pcb_mcp.audit import led_engineering_applies
class ScopeTests(unittest.TestCase):
 def test_no_led_board_does_not_require_led_supply(self):
  self.assertFalse(led_engineering_applies(dict(profile='schematic_experiment',components={'a':{'library_id':'Device:R'}},part_bindings={})))
 def test_declared_led_profile_still_checks_missing_parts(self):
  self.assertTrue(led_engineering_applies(dict(profile='led_series_experiment',components={},part_bindings={})))
 def test_led_symbol_without_binding_still_checked(self):
  self.assertTrue(led_engineering_applies(dict(profile='schematic_experiment',components={'a':{'library_id':'Device:LED'}},part_bindings={})))
 def test_imported_led_cannot_skip_checker(self):
  self.assertTrue(led_engineering_applies(dict(profile='schematic_experiment',components={'a':{'library_id':'EXT_VENDOR:Light'}},part_bindings={'a':{'kind':'led'}})))
if __name__=='__main__':unittest.main()
