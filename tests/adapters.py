"""Meaningful fail-closed checks on inheritance and physical pin identity."""
import json,tempfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pcb_mcp import backend

base='''(kicad_symbol_lib (version 20250114) (generator test)
 (symbol "Parent" (property "Value" "Parent")
  (symbol "Parent_0_1" (rectangle (start -1 -1) (end 1 1)))
  (symbol "Parent_1_1" (pin passive line (at 0 0 0) (length 2.54) (name "P") (number "[1,2]"))))
 (symbol "Child" (extends "Parent") (property "Value" "Child"))
 (symbol "CycleA" (extends "CycleB")) (symbol "CycleB" (extends "CycleA"))
 (symbol "Missing" (extends "Absent"))
 (symbol "Override" (extends "Parent") (symbol "Override_1_1"))
)'''
resolved=backend.inherited_definitions(base)
assert not resolved['Child'][1]
assert 'Child_1_1' in resolved['Child'][0] and 'Parent_1_1' not in resolved['Child'][0]
assert '(property "Value" "Child")' in resolved['Child'][0]
assert resolved['CycleA'][1] and resolved['Missing'][1] and resolved['Override'][1]
changed=backend.inherited_definitions(base.replace('(length 2.54)','(length 5.08)'))
assert backend.digest(changed['Child'][0].encode())!=backend.digest(resolved['Child'][0].encode())
with tempfile.TemporaryDirectory() as d:
    path=Path(d)/'Fixture.kicad_sym';path.write_text(base)
    st=path.stat(); rows=backend._catalog_library(str(path),st.st_mtime_ns,st.st_size)
    child=next(c for c in rows if c['symbol']=='Child')
    assert child['supported'] and child['inherited'] and child['pins'][0]['physical_numbers']==['1','2']
    path.write_text(base.replace('[1,2]','[1,1]'));st=path.stat()
    row=next(c for c in backend._catalog_library(str(path),st.st_mtime_ns,st.st_size) if c['symbol']=='Child')
    assert not row['supported'] and any('Overlapping' in reason for reason in row['unsupported_reasons'])
print(json.dumps(dict(passed=True,checks=['Real inherited geometry and field resolution','Parent changes invalidate dependent identity','Cycles, missing parents and graphical overrides rejected','Grouped pin identity preserved; overlapping physical numbers rejected']),indent=2))
