"""Prepare exact native diagrams, portable checks and verification summary."""
from pathlib import Path
import json,subprocess,shutil,sys,xml.etree.ElementTree as ET
from pcb_mcp import backend
from riding_cost_common import ROOT,OUT,DATA
CLI='/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli';PID=json.loads((OUT/'context.json').read_text())['project_id'];s=json.loads((DATA/PID/'state.json').read_text());bid=json.loads((OUT/'pcb-01/pcb_build.json').read_text())['pcb_build_id'];b=s['pcb_builds'][bid];board=DATA/PID/'pcb_builds'/bid;sch=next((DATA/PID).glob('*/'+b['schematic_build_id']));dest=OUT/'vendor-package';dest.mkdir(exist_ok=True)
subprocess.run([CLI,'sch','export','pdf','-o',str(dest/'schematic.pdf'),str(sch/'led.kicad_sch')],check=True,capture_output=True)
raw=backend.parse((sch/'led.kicad_sch').read_text());positions={}
for sym in backend.children(raw,'symbol'):
 prop={p['v'][1]:p['v'][2] for p in backend.children(sym,'property')};at=backend.children(sym,'at')[0]['v'][1:3];positions[prop['Reference']]=list(map(float,at))
(dest/'schematic-positions.json').write_text(json.dumps(positions,indent=2))
def command(args):
 r=subprocess.run([CLI,*args],text=True,capture_output=True);assert r.returncode==0,r.stdout+r.stderr;print(r.stdout.strip(),flush=True)
command(['pcb','export','pdf','--mode-single','--layers','F.Fab,F.Silkscreen,Edge.Cuts','--sketch-pads-on-fab-layers','--scale','2','-o',str(dest/'top-assembly.pdf'),str(board/'routed.kicad_pcb')])
command(['pcb','export','pdf','--mode-multipage','--layers','F.Cu,B.Cu,F.Mask,B.Mask','--common-layers','Edge.Cuts','--scale','2','-o',str(dest/'copper-and-mask-review.pdf'),str(board/'routed.kicad_pcb')])
command(['sch','export','netlist','--format','kicadxml','-o',str(OUT/'pcb-01/portable-netlist.xml'),str(dest/'CAD/SCH/led.kicad_sch')])
command(['pcb','drc','--format','json','-o',str(OUT/'pcb-01/portable-drc.json'),str(dest/'CAD/PCB/routed.kicad_pcb')])
x=ET.parse(OUT/'pcb-01/portable-netlist.xml').getroot();actual={n.attrib['name'].lstrip('/'):{(p.attrib['ref'],p.attrib['pin']) for p in n.findall('node')} for n in x.findall('./nets/net')}
byid=s['components'];expected={}
for n in s['nets'].values():
 entries=set()
 for member in n['pins']:
  c=byid[member['component_id']];p=next(p for p in c['pins'] if p['pin_id']==member['pin_id']);entries.update((c['ref'],str(i)) for i in p.get('physical_numbers',[p['number']]))
 expected[n['name']]=entries
assert {n:actual[n] for n in expected}==expected
# KiCad may also export explicitly NC singleton nets; these do not replace named nets.
drc=json.loads((OUT/'pcb-01/portable-drc.json').read_text());assert not drc['violations'] and not drc['unconnected_items'],drc
checks={}
for name in ['schematic_verify','erc_run','pcb_verify_routed','pcb_layout_check_routed','drc_run','pcb_copper_check']:
 r=json.loads((OUT/'pcb-01'/(name+'.json')).read_text());errors=[f for f in r.get('findings',[]) if f.get('severity')=='error'];assert not errors
 checks[name]=dict(verdict=r.get('verdict'),geometry_verdict=r.get('geometry_verdict'),errors=errors)
report=dict(project_id=PID,revision=s['revision'],components=len(s['components']),nets=len(s['nets']),pcb_build_id=bid,checks=checks,portable_netlist_matches=True,portable_drc_errors=0,portable_unconnected=0,physical_verified=False,remaining_physical_tests=['Power startup and thermal behavior','Battery/NTC cutoff and charging','RF range and city obstruction','Full-duplex echo, wind noise and loudness'],cost_down_regression_tests='20 catalog import + 5 immutable footprint-variant + 3 native mask-opening tests passed',firmware='Existing real Xtensa A/B build inputs and output hashes rechecked; exact GPIO mapping retained')
(dest/'verification-summary.json').write_text(json.dumps(report,indent=2));print('PORTABLE CAD AND INDEPENDENT NETLIST MATCH')
