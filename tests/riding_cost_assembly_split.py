"""Exact subsets of signed native CPL for machine and post-reflow assembly."""
import csv,json
from pathlib import Path
O=Path('work/riding-intercom/cost-down/vendor-package');g=json.loads((O/'assembly-geometry.json').read_text());rows=list(csv.DictReader((O/'positions.csv').read_text().splitlines()));assert len(rows)==91
for label,method,count in [('SMT','SMT',84),('THT','THT/manual after reflow',7)]:
 selected=[r for r in rows if g['components'][r['Ref']]['assembly_method']==method];assert len(selected)==count
 with (O/('positions-'+label+'.csv')).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(selected)
bom=list(csv.DictReader((O/'bom-grouped.csv').read_text().splitlines()));assert sum(int(r['Quantity']) for r in bom)==91
for row in bom:
 for ref in row['Designator'].split(','):assert row['Assembly']==g['components'][ref]['assembly_method']
assert len(list(csv.DictReader((O/'filled-capped-holes.csv').read_text().splitlines())))==0
assert not json.loads((O/'assembly-process.json').read_text())['declared_filled_capped_vias']
print('BOM/CPL:',84,'SMT +',7,'THT; no fill/cap locations')
