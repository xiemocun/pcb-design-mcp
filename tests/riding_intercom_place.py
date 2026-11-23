"""KiCad-native placement proposal for review, not manufacturing approval.

Run with KiCad's Python. Uses exact current source footprints and nets, places
functional groups, and rejects overlaps. Exported coordinates feed the public
MCP placement contract; no tracks, checks or approvals are fabricated.
"""
import json,math,sys
from pathlib import Path
import pcbnew as k
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from explicit_placement import native_check

root=Path('work/riding-intercom');state=json.loads((root/'data/e4569a2267ec4b0c8dbfe22e4bb60820/state.json').read_text());design=json.loads((root/'hardware-circuit/design.json').read_text())
board=k.BOARD();board.SetCopperLayerCount(2);byref={c['ref']:c for c in state['components'].values()};fps={}
assigned={(p['component_id'],p['pin_id']):n['name'] for n in state['nets'].values() for p in n['pins']}
nets={}
for n in state['nets'].values():
    obj=k.NETINFO_ITEM(board,n['name']);board.Add(obj);nets[n['name']]=obj
for ref,c in byref.items():
    binding=state['footprint_bindings'][c['id']];f=k.FootprintLoad(str(Path(binding['source']).parent),binding['footprint_id'].split(':')[1]);f.SetReference(ref);board.Add(f);fps[ref]=f
    for p in f.Pads():
        if not p.GetNumber():continue
        cp=next(x for x in c['pins'] if p.GetNumber() in x['physical_numbers']);n=assigned.get((c['id'],cp['pin_id']))
        if n:p.SetNet(nets[n])

anchors={'U1':(18.,20.,0),'U2':(59.,22.,0),'U3':(60.,40.,0),'U4':(12.,44.,0),'U5':(30.,46.,180),'U6':(36.,32.,0),'U7':(46.,45.,0),'U8':(78.,46.,0),'MK1':(58.,6.,0),'MK2':(80.,6.,0),'L1':(26.,45.,0),'J1':(6.,60.,90),'J2':(17.,60.,90),'J3':(28.,60.,90),'J4':(39.,60.,90),'J5':(80.,60.,90),'J6':(6.,4.,90),'J7':(55.,60.,90)}
hosts={'mcu':'U1','adc':'U2','dac':'U3','charger':'U4','boost':'U5','buck':'U6','audio_power':'U7','pa':'U8','mic1':'MK1','mic2':'MK2','aec':'U2','keys':'J7'}
placed={};centers={};used_supply={}
anchors['L1']=(25.5,45.,0)
anchors.update(C11=(36.,28.5,0),C12=(39.,33.75,90),R6=(43.,34.,90),R7=(42.,29.,90),C10=(45.5,34.,90))
def xy(p):return k.ToMM(p.x),k.ToMM(p.y)
def box(f):
    q=f.GetBoundingBox(False,False);return [k.ToMM(q.GetX()),k.ToMM(q.GetY()),k.ToMM(q.GetRight()),k.ToMM(q.GetBottom())]
def move(ref,x,y,angle):
    f=fps[ref];f.SetOrientationDegrees(angle);f.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)))
def overlap(a,b,gap=.7):return a[0]<b[2]+gap and a[2]>b[0]-gap and a[1]<b[3]+gap and a[3]>b[1]-gap
for ref,(x,y,angle) in anchors.items():
    move(ref,x,y,angle);a=box(fps[ref])
    if a[0]<0 or a[1]<0 or a[2]>90 or a[3]>65 or any(overlap(a,b) for b in placed.values()):raise ValueError('Fixed anchor invalid: '+ref)
    placed[ref]=a;centers[ref]=(x,y)
power={n['name'] for n in state['nets'].values() if n['kind']=='power'}
def priority(ref):
    ns={p.GetNetname() for p in fps[ref].Pads()}
    return (0 if ref.startswith('C') and 'GND' in ns and ns&power else 1 if ref.startswith('C') else 2,ref)
order=sorted((r for r in fps if r not in anchors),key=priority)
report=[]
for ref in order:
    f=fps[ref];group=design['groups'][ref];host=hosts[group];hf=fps[host]
    ns={p.GetNetname() for p in f.Pads() if p.GetNetname() and p.GetNetname()!='GND'}
    targets=[p for p in hf.Pads() if p.GetNetname() in ns and p.GetNumber()]
    is_bypass=priority(ref)[0]==0
    if is_bypass and targets:
        target=min(targets,key=lambda p:(used_supply.get((host,p.GetNumber()),0),p.GetNumber()));targets=[target];used_supply[(host,target.GetNumber())]=used_supply.get((host,target.GetNumber()),0)+1
    if not targets:
        targets=[p for r in placed if design['groups'][r]==group for p in fps[r].Pads() if p.GetNetname() in ns and p.GetNetname() not in power and p.GetNumber()]
    tx,ty=xy(targets[0].GetPosition()) if targets else centers[host]
    if group=='aec':tx,ty=70.,29.
    choices=[];grid=4 if is_bypass else 2
    # Grid bounds limit search work. Physical native envelopes limit acceptance.
    for angle in [0,90,180,270]:
        for xi in range(max(4*grid,round((tx-12)*grid)),min(86*grid,round((tx+12)*grid))+1):
            for yi in range(max(4*grid,round((ty-12)*grid)),min(60*grid,round((ty+12)*grid))+1):
                x,y=xi/grid,yi/grid;move(ref,x,y,angle);a=box(f)
                if a[0]<1.5 or a[1]<1.5 or a[2]>88.5 or a[3]>63.5 or any(overlap(a,b,1.15 if r in ['U2','U3','U4','U5'] and not (is_bypass and r==host) else .7) for r,b in placed.items()):continue
                distance=min((math.dist(xy(p.GetPosition()),xy(q.GetPosition())) for p in f.Pads() for q in targets if p.GetNetname()==q.GetNetname()),default=math.dist((x,y),(tx,ty)))
                if is_bypass:
                    gd=min((math.dist(xy(p.GetPosition()),xy(q.GetPosition())) for p in f.Pads() if p.GetNetname()=='GND' for q in hf.Pads() if q.GetNetname()=='GND'),default=999.)
                    distance=max(distance,gd)
                # Grounded bypasses get pin locality first. Remaining components
                # stay near their functional group instead of clustering all V5.
                score=distance+.15*math.dist((x,y),(tx,ty))
                choices.append((score,x,y,angle,distance))
    if not choices:raise ValueError('No nonoverlapping proposal for '+ref)
    _,x,y,angle,distance=min(choices);move(ref,x,y,angle);placed[ref]=box(f);centers[ref]=(x,y);anchors[ref]=(x,y,angle)
    report.append(dict(reference=ref,group=group,host=host,pad_distance_mm=distance))
bc=dict(width_mm=90.,height_mm=65.)
native_check(board,k,{'board_constraints':bc})
result=dict(width_mm=90.,height_mm=65.,placements=[dict(component_id=byref[r]['id'],x_mm=float(x),y_mm=float(y),rotation_deg=int(a),reason='Functional '+design['groups'][r]+' group; source footprint envelopes and local connected pads; requires actual routed layout review') for r,(x,y,a) in sorted(anchors.items())],measurements=report,qualification='unrouted placement proposal; no power-loop, acoustic or RF acceptance')
(root/'hardware-circuit/placement-proposal.json').write_text(json.dumps(result,indent=2))
from pcb_worker import summary
(root/'hardware-circuit/placement-native.json').write_text(json.dumps(summary(board),indent=2))
k.SaveBoard(str(root/'hardware-circuit/placement-native.kicad_pcb'),board)
print('Native placement proposal',len(anchors),'components, 90x65mm; no overlaps')
