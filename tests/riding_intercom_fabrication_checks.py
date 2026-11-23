"""Independent Excellon coordinate/tool crosscheck against native PCB holes."""
import json,re,hashlib
from pathlib import Path
root=Path('work/riding-intercom');out=root/'vendor-review';fab=out/'fabrication';expected=json.loads((root/'hardware-pcb-10/drill-reference.json').read_text());actual=[];tools={};tool=None
raw=(fab/'routed.drl').read_text();assert 'METRIC' in raw and 'G90' in raw and 'decimal' in raw
for line in raw.splitlines():
 m=re.fullmatch(r'T(\d+)C([\d.]+)',line)
 if m:tools[m[1]]=float(m[2]);continue
 m=re.fullmatch(r'T(\d+)',line)
 if m:tool=m[1];continue
 m=re.fullmatch(r'X(-?[\d.]+)Y(-?[\d.]+)',line)
 if m:actual.append(dict(x=float(m[1]),y=float(m[2]),diameter=tools[tool]))
assert len(actual)==len(expected)==213,(len(actual),len(expected));remaining=actual[:];maxerr=0.
for e in expected:
 hits=[a for a in remaining if abs(a['diameter']-e['diameter'])<1e-9 and abs(a['x']-e['x'])<=.000501 and abs(a['y']-e['y'])<=.000501];assert len(hits)==1,(e,hits);a=hits[0];remaining.remove(a);maxerr=max(maxerr,abs(a['x']-e['x']),abs(a['y']-e['y']))
assert not remaining
layers={'F_Cu':'Copper,L1,Top','B_Cu':'Copper,L2,Bot','F_Paste':'Paste,Top','B_Paste':'Paste,Bot','F_Mask':'Soldermask,Top','B_Mask':'Soldermask,Bot','F_Silkscreen':'Legend,Top','B_Silkscreen':'Legend,Bot','Edge_Cuts':'Profile,NP'}
for name,function in layers.items():
 text=(fab/('routed-'+name+'.gbr')).read_text();assert '%TF.FileFunction,'+function in text,(name,function);assert '%MOMM*%' in text and '%FSLAX46Y46*%' in text and text.rstrip().endswith('M02*'),name
edge=(fab/'routed-Edge_Cuts.gbr').read_text();points=[(int(x)/1e6,int(y)/1e6) for x,y in re.findall(r'X(-?\d+)Y(-?\d+)D0[12]\*',edge)];assert set(points)=={(0.,0.),(90.,0.),(0.,-65.),(90.,-65.)},points
manifest=json.loads((out/'fabrication_manifest.json').read_text());assert manifest['real_human_approval'] is True
for name,h in manifest['files'].items():assert hashlib.sha256((fab/name).read_bytes()).hexdigest()==h,name
result=dict(passed=True,gerber_layers=9,drill_holes=213,all_drill_tools_and_centers_match_native_PCB=True,max_drill_serialization_axis_error_mm=maxerr,excellon_decimal_precision_mm=.001,board_outline_mm=[90,65],signed_export_manifest_matches_all_fabrication_files=True,physical_verified=False)
(out/'fabrication-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
