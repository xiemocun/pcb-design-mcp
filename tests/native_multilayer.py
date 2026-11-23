"""Real KiCad native stackup, inner-layer collisions, keepouts and export.

Run with KiCad's Python, not the service interpreter. All files are synthetic
development fixtures in a new isolated output directory.
"""
import argparse
import json
from pathlib import Path
import sys
import pcbnew as k

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/pcb_mcp'))
from stackup import apply, inspect, copper_layers
from explicit_placement import keepouts, local_vias


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    output=parser.parse_args().output.resolve();output.mkdir(parents=True,exist_ok=False)
    bc=json.loads((ROOT/'tests/fixtures/multilayer/stackup-four.json').read_text())
    bc.update(width_mm=60.,height_mm=40.,clearance_mm=.2,edge_clearance_mm=.5,
              min_drill_mm=.3,hole_to_hole_mm=.25,placement_profile='explicit',placements=[],
              copper_keepouts=[dict(x_min_mm=40.,x_max_mm=45.,y_min_mm=20.,y_max_mm=25.,
                                   no_tracks=True,no_vias=True,no_fills=True)],
              local_vias=[dict(net_id='g',x_mm=20.,y_mm=20.,diameter_mm=.6,drill_mm=.3,
                              filled_capped=False,reason='Synthetic inner-layer collision test')])
    board=k.BOARD();board.SetCopperLayerCount(4)
    for a,b in [((0,0),(60,0)),((60,0),(60,40)),((60,40),(0,40)),((0,40),(0,0))]:
        edge=k.PCB_SHAPE();edge.SetShape(k.SHAPE_T_SEGMENT);edge.SetLayer(k.Edge_Cuts)
        edge.SetStart(k.VECTOR2I(k.FromMM(a[0]),k.FromMM(a[1])))
        edge.SetEnd(k.VECTOR2I(k.FromMM(b[0]),k.FromMM(b[1])));edge.SetWidth(k.FromMM(.05));board.Add(edge)
    path=output/'native.kicad_pcb';k.SaveBoard(str(path),board);apply(path,bc)
    board=k.LoadBoard(str(path));k.SaveBoard(str(path),board)
    native=inspect(path,bc);assert native['passed'],native
    assert [board.GetLayerName(x) for x in board.GetEnabledLayers().CuStack()]==copper_layers(bc)
    assert k.ExportSpecctraDSN(board,str(output/'native.dsn'))
    from sexpr import parse,children
    dsn=parse((output/'native.dsn').read_text().replace('(string_quote ")','(string_quote QUOTE)'))
    dsn_layers=[x['v'][1] for x in children(children(dsn,'structure')[0],'layer')]
    assert dsn_layers==copper_layers(bc),dsn_layers
    state=dict(board_constraints=bc,nets={'g':dict(name='GND')})
    keepouts(board,k,state)
    area=list(board.Zones())[0]
    assert all(area.IsOnLayer(x) for x in board.GetEnabledLayers().CuStack())
    ground=k.NETINFO_ITEM(board,'GND');board.Add(ground)
    foreign=k.NETINFO_ITEM(board,'FOREIGN');board.Add(foreign)
    track=k.PCB_TRACK(board);track.SetLayer(k.In1_Cu);track.SetNet(foreign)
    track.SetStart(k.VECTOR2I(k.FromMM(18),k.FromMM(20)))
    track.SetEnd(k.VECTOR2I(k.FromMM(22),k.FromMM(20)));track.SetWidth(k.FromMM(.3));board.Add(track)
    try:local_vias(board,k,state)
    except ValueError as error:assert str(error).startswith('LOCAL_VIA_COLLISION'),str(error)
    else:raise AssertionError('Through via was allowed to short inner-layer foreign copper')
    assert not any(type(x)==k.PCB_VIA for x in board.GetTracks())
    board.RemoveNative(track);assert len(local_vias(board,k,state))==1
    board.SetCopperLayerCount(2);k.SaveBoard(str(output/'wrong-layer-count.kicad_pcb'),board)
    wrong=inspect(output/'wrong-layer-count.kicad_pcb',bc)
    assert not wrong['passed'] and 'PCB_COPPER_LAYERS_MISMATCH' in wrong['errors']
    result=dict(passed=True,engine=k.GetBuildVersion(),native_stackup=native,
                dsn_copper_layers=dsn_layers,inner_layer_foreign_via_collision_rejected=True,
                all_layer_keepout=True,layer_count_tamper_rejected=True,
                physical_verified=False,scope='synthetic development geometry, not heldout or manufactured board')
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
