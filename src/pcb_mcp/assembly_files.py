"""Actual-board assembly tables, included in the same immutable review as copper."""
import csv,io,json
from . import backend
from .common import require,write_json

def prepare(s,source,folder,bom):
    run=backend.command(['pcb','export','pos','--format','csv','--units','mm','--side','both','-o',str(folder/'positions.csv'),str(source/'routed.kicad_pcb')],folder)
    write_json(folder/'positions_command.json',run);require(run['exit_code']==0,'POSITION_EXPORT_FAILED','Actual KiCad placement export failed')
    rows=list(csv.DictReader((folder/'positions.csv').read_text().splitlines()))
    require({r['Ref'] for r in rows}=={r['reference'] for r in bom},'POSITION_BOM_MISMATCH','Assembly positions must cover every populated reference exactly once')
    require(len(rows)==len(bom),'DUPLICATE_POSITION','Duplicate assembly position')
    from .pcb_service import worker
    geometry=worker('assembly',source);write_json(folder/'assembly-geometry.json',geometry)
    groups={}
    for row in bom:
        key=(row['manufacturer'],row['mpn'],row['footprint'],row['value']);groups.setdefault(key,[]).append(row['reference'])
    output=io.StringIO();w=csv.writer(output);w.writerow(['Designator','Quantity','Value','Manufacturer','MPN','Footprint','Assembly'])
    for (maker,mpn,footprint,value),refs in sorted(groups.items()):
        methods={geometry['components'][ref]['assembly_method'] for ref in refs}
        require(len(methods)==1,'ASSEMBLY_METHOD_MISMATCH','Identical BOM parts have different native assembly attributes')
        method=methods.pop()
        w.writerow([','.join(refs),len(refs),value,maker,mpn,footprint,method])
    (folder/'bom-grouped.csv').write_text(output.getvalue())
    output=io.StringIO();w=csv.writer(output);w.writerow(['X_mm_PCB_right','Y_mm_PCB_down','Drill_X_mm','Drill_Y_mm','Host_pads','Process'])
    for h in geometry['fill_cap_holes']:w.writerow([h['x'],h['y'],h['drill_x_mm'],h['drill_y_mm'],','.join(h['hosts']),'Resin fill and copper cap (via-in-pad)'])
    (folder/'filled-capped-holes.csv').write_text(output.getvalue())
    bc=s['board_constraints']
    write_json(folder/'assembly-process.json',dict(board_size_mm=[bc['width_mm'],bc['height_mm']],layer_count=bc['layers'],thickness_mm=geometry['thickness_mm'],filled_capped_hole_count=len(geometry['fill_cap_holes']),coordinates='positions.csv: KiCad native board origin, millimetres, Y axis up; positive rotation counterclockwise; native library zero orientation, verify pin1 in assembly drawing',declared_filled_capped_vias=[v for v in bc.get('local_vias',[]) if v.get('filled_capped')],requirements=['No part substitutions without engineering review','Resin fill and copper cap all locations in filled-capped-holes.csv before reflow' if geometry['fill_cap_holes'] else 'No exposed solder-land drill holes: ordinary plated through vias, tent both sides; preserve native mask apertures without automatic opening of covered thermal holes','Full-board electrical net test before assembly','X-ray exposed-pad QFN and module joints; verify pin1 and connector polarity','CPL rotations use native KiCad footprint zero, not supplier-specific zero; assembler must confirm pin1 before placement']))
    return ['positions.csv','positions_command.json','bom-grouped.csv','assembly-process.json','assembly-geometry.json','filled-capped-holes.csv']
