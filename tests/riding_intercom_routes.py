"""Propose explicit short fanout copper from actual native pad positions.

No DRC waiver: deliberately narrow power fanouts must still be qualified by
the subsequent copper/branch checks before a manufacturing review.
"""
import json,sys
from pathlib import Path
import pcbnew as k
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from explicit_placement import local_routes,keepouts
root=Path('work/riding-intercom')
s=json.loads((root/'data/e4569a2267ec4b0c8dbfe22e4bb60820/state.json').read_text())
a=json.loads((root/'hardware-circuit/placement-native.json').read_text())
b=k.LoadBoard(str(root/'hardware-circuit/placement-native.kicad_pcb'));keepouts(b,k,s)
nets={n['name']:n['id'] for n in s['nets'].values()};rows=[]
def route(net,width,points,reason,via=False):
    row=dict(net_id=nets[net],width_mm=width,layer='F.Cu',points=[dict(x_mm=x,y_mm=y) for x,y in points],reason=reason,via_at_end=via)
    before={t.m_Uuid.AsString() for t in b.GetTracks()};s['board_constraints']['local_routes']=[row]
    try:local_routes(b,k,s)
    except ValueError:
        for t in list(b.GetTracks()):
            if t.m_Uuid.AsString() not in before:b.RemoveNative(t)
        return False
    rows.append(row);return True
for ref in ['U2','U3','U4','U5','U6']:
    fp=a['components'][ref];cx,cy=fp['position_mm']
    for p in fp['pads']:
        net=p['net']
        if not net or net=='GND':continue
        x,y=p['at_mm'];dx,dy=x-cx,y-cy
        width=.15 if ref in ['U2','U3'] else .25
        if ref in ['U2','U3'] and net=='A3V3':width=.2
        if ref=='U6':
            # SIL package pads run in two columns, regardless of y distance.
            endpoint=(34.5 if dx<0 else 38.0 if net=='BUCK_FB' else 37.5,y)
            if not route(net,width,[(x,y),endpoint],ref+'.'+p['number']+' direct power land escape',net=='BUCK_FB'):raise ValueError('Fixed U6 escape blocked')
            continue
        ok=False
        # Spread 0.4-mm-pitch pins to 0.7-mm via pitch before changing layer.
        # Merely staggering normal vias obstructs adjacent 0.15-mm traces.
        if abs(dx)>abs(dy):
            sign=1 if dx>0 else -1
            fan=[(x,y),(x+sign*.35,y),(x+sign*1.4,y+dy*.75)]
        else:
            sign=1 if dy>0 else -1
            fan=[(x,y),(x,y+sign*.35),(x+dx*.75,y+sign*1.4)]
        if route(net,width,fan,ref+'.'+p['number']+' expanded-pitch fanout with native copper clearance check',True):continue
        for distance in [.9,1.5,.65,1.2,1.8,2.1,2.4]:
            endpoint=(x+(1 if dx>0 else -1)*distance,y) if abs(dx)>abs(dy) else (x,y+(1 if dy>0 else -1)*distance)
            if route(net,width,[(x,y),endpoint],ref+'.'+p['number']+' short staggered via fanout checked against native copper',True):ok=True;break
        if not ok:print('No collision-free fanout candidate',ref,p['number'],net)
# Same-rail power pins of the buck module connect outside its body.
assert route('V5',.6,[(34.5,31),(34.5,32)],'U6 VIN/EN common rail outside fine-pitch lands')
assert route('D3V3',.35,[(37.5,32.5),(37.5,34),(34.5,34),(34.5,33)],'U6 three OUT lands joined outside body')
assert route('V5',.6,[(34.5,31),(34.5,29.05),(35.05,28.5)],'U6 input capacitor positive connection on front copper')
assert route('D3V3',.35,[(37.5,34),(38.2,34.7),(39,34.7)],'U6 output capacitor positive connection on front copper')
assert route('GND',.25,[(36.625,31),(37.5,31)],'U6 GND land to local return backbone')
assert route('GND',.25,[(36.625,31.5),(37,31.5),(37.5,31)],'U6 second GND land to local return backbone')
assert route('GND',.35,[(37.5,31),(37.5,28.5),(36.95,28.5)],'U6 input capacitor ground connected locally on front copper')
assert route('GND',.35,[(37.5,31),(39,31),(39,32.8)],'U6 output capacitor ground connected locally on front copper')
(root/'hardware-circuit/local-routes.json').write_text(json.dumps(rows,indent=2))
print(len(rows),'proposed local routes; native collision and DRC required')
