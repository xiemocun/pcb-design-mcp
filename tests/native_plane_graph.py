"""Real-board graph check plus a deliberately removed isolated-island bridge."""
import pcbnew as k,json,sys
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from ground_planes import definition,stitching_graph,inspect
root=Path('work/riding-intercom');p=root/'routing-workbench/switch25.kicad_pcb';s=json.loads((root/'data/e4569a2267ec4b0c8dbfe22e4bb60820/state.json').read_text());s['pcb_copper_policy']=json.loads((root/'hardware-circuit/copper-policy-proposal.json').read_text());b=k.LoadBoard(str(p));d=definition(s);good=stitching_graph(b,k,d)
assert good['connected'],good
bridge=next(t for t in b.GetTracks() if type(t)==k.PCB_VIA and t.GetNetname()=='GND' and abs(k.ToMM(t.GetPosition().x)-55)<.001 and abs(k.ToMM(t.GetPosition().y)-21.9)<.001)
b.RemoveNative(bridge);bad=stitching_graph(b,k,d);assert not bad['connected'],bad
b=k.LoadBoard(str(p));report=inspect(p,b,k,s);print('Valid polygon graph:',good['polygon_count'],'polygons; deleted actual bridge rejected. Ground inspection:',report['status'],report['errors'])
(root/'routing-workbench/plane-graph-check.json').write_text(json.dumps(dict(actual_graph=good,removed_bridge_graph=bad,ground_inspection=report),indent=2))
